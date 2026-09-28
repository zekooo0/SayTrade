"""Summary boundary: the actions in, one plain line out.

The summary is written from the CHANGES, never from the transcript. The
transcript is what the model read, and it carries the transcription's own
errors; the changes are the facts the trade is made of. So this call is handed
the numbers that are about to be applied and nothing else, which is what makes
the line unable to repeat a mishearing.

A model writes the line when one is available; app/services/summary.py holds a
deterministic template behind it, so a note always has a summary.
"""

import asyncio
import json
from typing import Any, Mapping, Protocol, Sequence

SUMMARY_SYSTEM_PROMPT = """You write ONE short line for a trading journal. It says what the trader did.

You are given the actions only, as JSON. You are NOT given the trader's words, and you must not ask for them.

Rules:
1. Describe only the actions you were given. Invent nothing.
2. Use only the numbers you were given. Never compute, never round, never write a number that is not in the actions.
3. No advice, no opinion, no reason, no emotion. Never write "because", "should", or "good".
4. One line. No bullet points, no markdown, no quotation marks around the whole line.
5. Write in the language you are asked for: "en" is English, "ar" is Arabic.
6. Name the instrument by the ticker you were given. If no ticker was given, do not name one.
7. A change with no number yet (a value the trader has not given) is described without one. Never fill the gap.

Return only the line."""

OBSERVATION_SYSTEM_PROMPT = """You write ONE short line for a trading journal.

The trader recorded a note that holds NO position change — an observation, a plan, or a thought. Say what they saw or decided, in one line.

Rules:
1. There is no action to describe, so never write one. Never say they bought or sold anything.
2. Invent nothing. Use only numbers that appear in their words: never compute, never round, never add a figure.
3. If part of their words is unclear or garbled, leave that part out. Do not repeat noise.
4. No advice, no opinion, no reason, no emotion. Never write "because", "should", or "good".
5. One line, but keep the whole thought. Do not drop the second half: what they decided matters as much as what they saw.
6. Write in the language you are asked for: "en" is English, "ar" is Arabic.

Examples:
"today I saw a reverse candle, I will wait for the next day candle" -> "Saw a reverse candle today; waiting for tomorrow's candle."
"شفت شمعة انعكاس النهاردة وهستنى شمعة بكرة" -> "شاف شمعة انعكاس النهاردة وهينتظر شمعة بكرة."

Return only the line."""


class Summarizer(Protocol):
    async def summarize(
        self,
        changes: Sequence[Mapping[str, Any]],
        language: str,
        symbol: str | None = None,
    ) -> str | None: ...

    async def summarize_observation(self, text: str, language: str) -> str | None: ...


class OpenAISummarizer:
    """One short chat completion over the action list. No transcript, ever."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY)")
        return OpenAI(api_key=self.api_key)

    async def summarize(
        self,
        changes: Sequence[Mapping[str, Any]],
        language: str,
        symbol: str | None = None,
    ) -> str | None:
        client = self._client()
        payload = json.dumps(
            {
                "language": language,
                "symbol": symbol,
                "actions": [dict(c) for c in changes],
            },
            ensure_ascii=False,
            default=str,
        )

        def _call() -> str:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
                temperature=0,
            )
            return response.choices[0].message.content or ""

        line = await asyncio.to_thread(_call)
        return line.strip().strip('"').strip() or None

    async def summarize_observation(self, text: str, language: str) -> str | None:
        """A note with no position change: the trader's words are all there is."""
        client = self._client()
        payload = json.dumps({"language": language, "note": text}, ensure_ascii=False)

        def _call() -> str:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
                temperature=0,
            )
            return response.choices[0].message.content or ""

        line = await asyncio.to_thread(_call)
        return line.strip().strip('"').strip() or None
