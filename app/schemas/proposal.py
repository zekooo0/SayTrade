"""The AI proposal for one note.

The model does not name a ticker and have it believed. It proposes companies —
``security_candidates`` — and the symbols table decides which of those actually
trade in the market the note was recorded into. A candidate whose ticker is not
in that market is dropped, so a misheard word cannot become a confident, wrong
symbol. What survives is a proposal the trader reviews and approves.

Every field is optional at proposal time. The rule from the product spec is
hard: the model must never invent a value it was not given. Completeness is
therefore enforced at approval, in app/services/notes.py, with an error that
names exactly what is missing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Kind = Literal["OPEN", "ADD", "TRIM", "STOP", "TARGET", "CLOSE"]


class ChangeProposal(BaseModel):
    """One proposed change to a position.

    ``fraction`` carries a spoken share of the position ("half", "a third").
    The backend turns it into shares against the real position — the model
    never does position arithmetic, because it does not know the position.
    """

    kind: Kind
    occurred_at: datetime | None = None

    shares: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    stop: Decimal | None = Field(default=None, gt=0)
    target: Decimal | None = Field(default=None, gt=0)

    # 0 < fraction < 1: "half" -> 0.5.
    fraction: Decimal | None = Field(default=None, gt=0, lt=1)


class SecurityCandidate(BaseModel):
    """One company the model thinks the note is about.

    A proposal, never a fact. ``ticker`` is checked against the symbols table for
    the note's market and dropped if it does not trade there; ``name`` is what
    the lookup falls back to when the model named no ticker, or named one that
    failed that check. ``aliases`` are the words the trader actually used —
    carried so the review can show them, and written nowhere.
    """

    exchange: str | None = None
    ticker: str | None = None
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("exchange", "ticker", "name", mode="before")
    @classmethod
    def _an_absent_value_is_none(cls, value):
        """The string "null" is absence, not a value.

        gpt-4o-mini answers with the literal string when it means nothing, and
        left alone it becomes a trade whose ticker is NULL — which the trader
        then has to find and delete.
        """
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "null",
            "none",
            "n/a",
        }:
            return None
        return value

    @field_validator("aliases", mode="before")
    @classmethod
    def _drop_empty_aliases(cls, value):
        if not isinstance(value, list):
            return []
        return [
            alias.strip()
            for alias in value
            if isinstance(alias, str) and alias.strip()
        ]


class NoteProposal(BaseModel):
    """What the AI read in one note.

    ``language`` is the language the note is spoken in ("en" or "ar"). The
    action summary is written in it.

    There is no ``summary`` field: the summary is built from the changes, not
    read out of the words, so the model is never asked for one.

    An empty ``changes`` list is valid and means "an observation, no position
    change" — e.g. "I saw a reverse candle, I will wait".
    """

    language: str | None = None
    security_candidates: list[SecurityCandidate] = Field(default_factory=list)
    changes: list[ChangeProposal] = Field(default_factory=list)
