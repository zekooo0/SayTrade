"""Turn a spoken company name into the ticker that trades in the note's market.

This is the whole point of the symbols table: the model hears the company and
writes its name in English, and the name becomes a ticker here — against the
vendored reference for that market, never against the model's memory.

The failure mode is a miss, not a wrong ticker. A name that matches nothing, or
that fits more than one company, returns None and the trader types the ticker
once. A confident wrong ticker is the thing this replaces.

Matching is word-level and lives in Python, not SQL. Character trigrams were
tried and rejected: "Elsewedy" against "Elswedy" — one letter apart, the exact
case this exists for — scores 0.16 against a long registered name, because the
metric punishes length. Comparing the words themselves handles it, and it is
testable without a database.

Every decision is logged. A null symbol has four possible causes — the model
named no company, it named one that does not trade here, the name did not match
the reference, or it matched more than one company — and the log says which.
"""

from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.log import get_logger
from app.models import Symbol, SymbolAlias

log = get_logger("saytrade.symbols")

# A name must score this well to be considered a match at all.
MIN_SCORE = 0.75

# A single word has nothing to be checked against, so it must be near-perfect.
# "Apple" against "Advanced Pharmaceutical Packaging" scored 0.75 without this.
MIN_SCORE_SINGLE_WORD = 0.9

# Below this many characters a word is not a word. SequenceMatcher rates "app"
# against "apple" at 0.75, which is how a packaging company became Apple.
MIN_WORD_LENGTH = 4

# And must beat the best rival company by this much. A name that ties with
# another company returns nothing: the trader decides, not a coin toss.
MIN_MARGIN = 0.10


def normalize(name: str) -> str:
    """Lowercase, single-spaced, punctuation dropped.

    Both sides are stored and compared in this form, so the seed and the lookup
    can never disagree about what "El Sewedy Electric" is.
    """
    cleaned = "".join(
        char if (char.isalnum() or char.isspace()) else " " for char in name.lower()
    )
    return " ".join(cleaned.split())


def _word_score(word: str, other: str) -> float:
    """How well one word of the spoken name matches one word of the registered
    name. Short words must be equal: "app" is not "apple"."""
    if word == other:
        return 1.0
    if min(len(word), len(other)) < MIN_WORD_LENGTH:
        return 0.0
    return SequenceMatcher(None, word, other).ratio()


def score(key: str, alias: str) -> float:
    """How well a spoken name matches one registered name, 0 to 1.

    Exact first, then containment, then word by word: each word of the spoken
    name is compared to its closest word in the registered name, and the scores
    are averaged. "Elsewedy Electric" against "Elswedy Electric" scores high on
    the words that matter, and a word the registered name does not have pulls it
    down — which is what keeps a padded name from matching everything.
    """
    if key == alias:
        return 1.0
    if key in alias or alias in key:
        return 0.95

    key_words = key.split()
    alias_words = alias.split()
    if not key_words or not alias_words:
        return 0.0

    total = sum(
        max(_word_score(word, other) for other in alias_words) for word in key_words
    )
    return total / len(key_words)


def best_match(key: str, aliases: dict[str, str]) -> str | None:
    """The ticker for a spoken name, or None when nothing wins clearly.

    `aliases` maps a normalised name to a ticker. Pure function: no database, so
    the matching rules are tested directly.
    """
    if not key:
        return None
    if key in aliases:
        log.info("  %r is an exact match -> %s", key, aliases[key])
        return aliases[key]

    scored = sorted(
        ((score(key, alias), ticker) for alias, ticker in aliases.items()),
        reverse=True,
    )
    if not scored:
        log.info("  %r: no names to match against", key)
        return None

    log.debug("  %r: best five of %d names:", key, len(scored))
    for value, ticker in scored[:5]:
        log.debug("    %.3f  %s", value, ticker)

    top_score, top_ticker = scored[0]
    threshold = MIN_SCORE_SINGLE_WORD if len(key.split()) == 1 else MIN_SCORE
    if top_score < threshold:
        log.info(
            "  %r: best score %.3f is under %.2f (closest was %s) -> no match",
            key,
            top_score,
            threshold,
            top_ticker,
        )
        return None

    rivals = [value for value, ticker in scored if ticker != top_ticker]
    if rivals and rivals[0] > top_score - MIN_MARGIN:
        log.info(
            "  %r: ambiguous, %.3f (%s) against a rival at %.3f -> no match",
            key,
            top_score,
            top_ticker,
            rivals[0],
        )
        return None

    log.info("  %r -> %s (score %.3f)", key, top_ticker, top_score)
    return top_ticker


async def aliases_for(session: AsyncSession, market: str) -> dict[str, str]:
    """Every name the market's companies are known by, as name -> ticker.

    A few thousand rows per market, seeded and never written at runtime, so this
    is a cheap read on the note's path.
    """
    rows = await session.execute(
        select(SymbolAlias.alias, SymbolAlias.ticker).where(
            SymbolAlias.market == market
        )
    )
    aliases = dict(rows.all())
    log.debug("market %s: %d names loaded for matching", market, len(aliases))
    return aliases


async def resolve_symbol(
    session: AsyncSession, market: str | None, company: str | None
) -> Symbol | None:
    """The symbol a company name refers to in this market, or None.

    None is a normal answer: no market, no name, no match, or a name that fits
    more than one company.
    """
    if not market or not company:
        log.debug("nothing to resolve: market=%r name=%r", market, company)
        return None
    ticker = best_match(normalize(company), await aliases_for(session, market))
    if ticker is None:
        return None
    return await session.scalar(
        select(Symbol).where(Symbol.market == market, Symbol.ticker == ticker)
    )


async def resolve_candidates(
    session: AsyncSession, market: str | None, candidates: list[dict]
) -> list[Symbol]:
    """The symbols the model's candidates actually refer to, verified.

    A candidate's ticker counts only if it exists in this market's table — that
    is what stops a model naming a ticker from another exchange. When the ticker
    is absent or fails that check, the candidate's NAME is looked up instead,
    which is how an Arabic company name becomes a ticker: the model translates
    the name, the table decides what trades here.

    Nothing is persisted. The candidates die with the note.
    """
    if not market or not candidates:
        log.info(
            "nothing to verify: market=%r, %d candidate(s) proposed",
            market,
            len(candidates),
        )
        return []

    log.info("verifying %d candidate(s) against market %s", len(candidates), market)
    found: dict[str, Symbol] = {}
    for candidate in candidates:
        log.info(
            "  candidate: exchange=%r ticker=%r name=%r aliases=%r",
            candidate.get("exchange"),
            candidate.get("ticker"),
            candidate.get("name"),
            candidate.get("aliases"),
        )
        symbol = None

        ticker = (candidate.get("ticker") or "").strip().upper()
        if ticker:
            symbol = await session.scalar(
                select(Symbol).where(Symbol.market == market, Symbol.ticker == ticker)
            )
            if symbol is not None:
                log.info("    %s trades in %s -> kept (%s)", ticker, market, symbol.name)
            else:
                log.info(
                    "    %s is not in %s's list -> dropped, trying the name instead",
                    ticker,
                    market,
                )

        if symbol is None:
            # The name first — it is what the model is most likely to have right.
            for spoken in [candidate.get("name"), *(candidate.get("aliases") or [])]:
                if not spoken:
                    continue
                log.info("    looking up %r in %s", spoken, market)
                symbol = await resolve_symbol(session, market, spoken)
                if symbol is not None:
                    log.info("    %r -> %s (%s)", spoken, symbol.ticker, symbol.name)
                    break
            if symbol is None:
                log.info("    nothing matched -> this candidate is dropped")

        if symbol is not None:
            found[symbol.ticker] = symbol

    log.info(
        "verified: %s",
        ", ".join(sorted(found)) if found else "no symbol resolved",
    )
    return list(found.values())
