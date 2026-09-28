"""The one-line summary of a note: the actions, not the words.

A summary is built from the changes, never from the transcript. The transcript
is what the model read, and it carries the transcription's own errors — a
summary written from it repeats them. The changes are the facts the trade is
made of, so a line written from them cannot be wrong about what happened.

The line is written by a model (app/providers/summary.py) and checked against
the facts before it is kept: every number in it must be a number it was given.
Anything else — a refused call, a line that invents a number — falls back to a
deterministic template, so a note always has a summary and it is always true.

Called twice in a note's life: when the reading is done (so the review screen
shows the line) and at approval, from the rows that were actually applied, so
the summary can never describe a proposal the trader did not approve.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, Sequence

ARABIC_START, ARABIC_END = "\u0600", "\u06ff"

# A number as it appears in a written line: 100, 10.5, 10,50.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


class Summarizer(Protocol):
    async def summarize(
        self,
        changes: Sequence[Mapping[str, Any]],
        language: str,
        symbol: str | None = None,
    ) -> str | None: ...

    async def summarize_observation(self, text: str, language: str) -> str | None: ...


def detect_language(text: str | None) -> str:
    """The language of the trader's words: "ar" for Arabic, else "en"."""
    if text and any(ARABIC_START <= ch <= ARABIC_END for ch in text):
        return "ar"
    return "en"


def language_of(read: str | None, text: str | None) -> str:
    """The language to write the summary in.

    What the model reported for the note wins; the trader's own words are the
    fallback, so a note always gets a line in its own language.
    """
    if read and read.strip():
        value = read.strip().lower()
        if value.startswith("ar"):
            return "ar"
        if value.startswith("en"):
            return "en"
    return detect_language(text)


def _get(change: Any, key: str) -> Any:
    """Read a field from either a proposal dict or an applied Change row."""
    if isinstance(change, Mapping):
        return change.get(key)
    return getattr(change, key, None)


def _num(value: Any) -> str | None:
    """A number as a plain string: 10.20 -> "10.2", 100 -> "100"."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or None


def facts(changes: Sequence[Any]) -> list[dict[str, str]]:
    """The action facts, and nothing else.

    Only the kind and the numbers the trader gave. No timestamps, no transcript,
    no position size: whatever is here is something the summary may say.
    """
    out: list[dict[str, str]] = []
    for change in changes:
        kind = (_get(change, "kind") or "").strip().upper()
        if not kind:
            continue
        row = {"kind": kind}
        for key in ("shares", "price", "stop", "target"):
            value = _num(_get(change, key))
            if value is not None:
                row[key] = value
        out.append(row)
    return out


def _one_line(fact: Mapping[str, str], symbol: str | None, language: str) -> str:
    kind = fact["kind"]
    shares, price = fact.get("shares"), fact.get("price")
    stop, target = fact.get("stop"), fact.get("target")

    if language == "ar":
        name = symbol or "المركز"
        if kind == "OPEN":
            line = f"فتح {name}: شراء {shares} سهم بسعر {price}" if shares and price else f"فتح {name}"
            if stop:
                line += f"، وقف {stop}"
            if target:
                line += f"، هدف {target}"
            return line
        if kind == "ADD":
            line = f"إضافة {shares} سهم إلى {name}" if shares else f"إضافة إلى {name}"
            return line + (f" بسعر {price}" if price else "")
        if kind == "TRIM":
            line = f"بيع {shares} من {name}" if shares else f"بيع من {name}"
            return line + (f" بسعر {price}" if price else "")
        if kind == "STOP":
            return f"نقل وقف {name} إلى {stop}" if stop else f"تغيير وقف {name}"
        if kind == "TARGET":
            return f"نقل هدف {name} إلى {target}" if target else f"تغيير هدف {name}"
        if kind == "CLOSE":
            return f"إغلاق {name}" + (f" بسعر {price}" if price else "")
        return f"{name}: {kind}"

    name = symbol or "the trade"
    if kind == "OPEN":
        line = f"Opened {name}: bought {shares} shares at {price}" if shares and price else f"Opened {name}"
        if stop:
            line += f", stop {stop}"
        if target:
            line += f", target {target}"
        return line
    if kind == "ADD":
        line = f"Added {shares} shares of {name}" if shares else f"Added to {name}"
        return line + (f" at {price}" if price else "")
    if kind == "TRIM":
        line = f"Sold {shares} of {name}" if shares else f"Sold part of {name}"
        return line + (f" at {price}" if price else "")
    if kind == "STOP":
        return f"Moved the {name} stop to {stop}" if stop else f"Changed the {name} stop"
    if kind == "TARGET":
        return f"Moved the {name} target to {target}" if target else f"Changed the {name} target"
    if kind == "CLOSE":
        return f"Closed {name}" + (f" at {price}" if price else "")
    return f"{name}: {kind}"


def template_summary(
    changes: Sequence[Any], symbol: str | None = None, language: str = "en"
) -> str | None:
    """The deterministic line. None when there is no action to describe."""
    rows = facts(changes)
    if not rows:
        return None
    return "; ".join(_one_line(row, symbol, language) for row in rows)


def grounded(line: str, rows: Sequence[Mapping[str, str]]) -> bool:
    """Is every number in the line a number it was given?

    The check that keeps a written line honest: the model may phrase freely, but
    it may not introduce a figure. A line that fails falls back to the template.
    """
    allowed = {value for row in rows for key, value in row.items() if key != "kind"}
    for token in _NUMBER.findall(line):
        if _num(token) not in allowed:
            return False
    return True


def grounded_in(line: str, source: str) -> bool:
    """Is every number in the line a number that appears in the source text?

    The same honesty check for an observation, where the trader's words are the
    only thing the line may draw on.
    """
    allowed = {_num(token) for token in _NUMBER.findall(source)}
    for token in _NUMBER.findall(line):
        if _num(token) not in allowed:
            return False
    return True


def _first_sentence(text: str, limit: int = 140) -> str:
    """The trader's own words, cut to one line.

    The floor under an observation: a note with no action still gets a line, and
    when nothing writes one, their own sentence is the honest answer.
    """
    flat = " ".join(text.split())
    for end in (". ", "? ", "! ", "؟ ", "۔ "):
        if end in flat:
            flat = flat.split(end)[0] + end.strip()
            break
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return flat


async def write_summary(
    changes: Sequence[Any],
    symbol: str | None = None,
    language: str = "en",
    summarizer: Summarizer | None = None,
    text: str | None = None,
) -> str | None:
    """The note's one-line summary.

    A note WITH changes gets a line about the actions, built from the facts. A
    note with NO change gets a line about what the trader saw, built from their
    own words, because for an observation the words are the only thing there is
    — and it may not invent a move or a number.

    A model writes either line. Every failure — no summarizer, a refused call, a
    line that invents a number — lands on a floor that cannot lie: the template
    for actions, the trader's own sentence for an observation.
    """
    rows = facts(changes)

    if rows:
        fallback = template_summary(rows, symbol, language)
        if summarizer is None:
            return fallback
        try:
            written = await summarizer.summarize(rows, language, symbol)
        except Exception:
            return fallback
        written = (written or "").strip()
        if not written or not grounded(written, rows):
            return fallback
        return written

    # An observation: there is no action to describe, so the trader's words are
    # the source. The line says what they saw; it never invents a position move.
    if not text or not text.strip():
        return None
    fallback = _first_sentence(text)
    if summarizer is None:
        return fallback
    try:
        written = await summarizer.summarize_observation(text, language)
    except Exception:
        return fallback
    written = (written or "").strip()
    if not written or not grounded_in(written, text):
        return fallback
    return written
