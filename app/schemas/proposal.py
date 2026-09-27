"""The AI proposal for one note.

Every field is optional at proposal time. The rule from the product spec is
hard: the model must never invent a value it was not given. Completeness is
therefore enforced at approval, in app/services/notes.py, with an error that
names exactly what is missing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

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


class NoteProposal(BaseModel):
    """What the AI read in one note.

    ``symbol`` is the instrument. It is required only when a change opens a
    trade (or when a follow-up must be matched to an open trade by symbol).
    An empty ``changes`` list is valid and means "an observation, no position
    change" — e.g. "I saw a reverse candle, I will wait".
    """

    summary: str | None = None
    symbol: str | None = None
    changes: list[ChangeProposal] = Field(default_factory=list)
