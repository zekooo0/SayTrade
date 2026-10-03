"""What the trader said would happen after a close, and whether it did.

Made when a trade closes: "I exited because I expect this setup to fail and
reach my failure point." The level defaults to the trade's stop, because that IS
the failure point the story describes; the trader names one only when it differs.

Resolved by a later note on the same trade. That is all the identification
needed: a follow-up note always arrives from the trade's own page, so its
trade_id already says which prediction it is about. The trader's words are
relied on only when opening a new trade.

The result is the one stored value in this app that is not derived. It cannot be
derived: it is a fact about what the price did AFTER the close, and the app holds
no prices at all. Everything else — position size, R multiple, win rate, a
note's symbol — comes out of the change rows.

Trade result and prediction result are separate dimensions and never interact. A
LOSS with a CORRECT prediction is a valid and important outcome.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PREDICTION_RESULTS = ("PENDING", "CORRECT", "INCORRECT")


class Prediction(Base):
    __tablename__ = "predictions"

    __table_args__ = (
        # One prediction per trade. A closed trade cannot be reopened, and a
        # second position in the same stock is a new trade with its own row.
        UniqueConstraint("trade_id", name="uq_predictions_trade"),
        Index("ix_predictions_user_result", "user_id", "result"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    trade_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trades.id"), nullable=False
    )
    # The note that carried the prediction — the one that closed the trade.
    note_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notes.id"), nullable=True
    )

    # The price the trader expects to be reached. Taken from the trade's stop
    # when he does not name one; null only when the trade had no stop, in which
    # case there is nothing to judge and no prediction is created at all.
    level: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    # Which way price has to move to reach that level: "BELOW" for a long that
    # fails, "ABOVE" for a move up. Stored rather than inferred so the judgement
    # never depends on arithmetic done later.
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The trader's own words for why. Kept as text: the reason is not a change
    # and not a value the app can compute.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    result: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="PENDING"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_note_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notes.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
