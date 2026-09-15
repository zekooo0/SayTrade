import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

OUTCOME_RESULTS = ("WIN", "LOSS", "BREAKEVEN")


class TradeOutcome(Base):
    """How a completed Trade finished. At most one per Trade.

    Append-only like the other ledger tables: no updated_at.

    P&L and R multiple are deliberately NOT stored here: the spec derives
    them from executions, so there is no duplicated value to go stale.
    `result` is the trader-declared classification and is never silently
    overwritten by derived numbers; analytics can flag disagreement instead.
    """

    __tablename__ = "trade_outcomes"

    __table_args__ = (
        # Nullable, but when present it must be 1-4. A NULL passes a CHECK
        # automatically, so the range only constrains actual values.
        CheckConstraint("type BETWEEN 1 AND 4", name="ck_trade_outcomes_type"),
        # One outcome per trade. This also creates a unique index, which
        # doubles as the index for the foreign key lookups.
        UniqueConstraint("trade_id", name="uq_trade_outcomes_trade_id"),
        # No ondelete on the FK: Postgres defaults apply, so a Trade that
        # already has an outcome cannot be deleted.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    trade_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("trades.id"),
        nullable=False,
    )

    type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    result: Mapped[str] = mapped_column(String(20), nullable=False)

    lesson: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # No default: the trader says when the outcome was recorded.
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )