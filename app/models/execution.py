import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SIDES = ("BUY", "SELL")


class Execution(Base):
    """A single fill: something actually bought or sold against a Trade.

    Append-only by design: no updated_at, because a fill is a record of
    something that happened and should not be edited in place.

    Whether a BUY/SELL increases or decreases the position depends on the
    Trade direction (LONG vs SHORT); the spec puts that validation in the
    backend, not in a row-level CHECK.
    """

    __tablename__ = "executions"

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_executions_quantity_positive"),
        CheckConstraint("price > 0", name="ck_executions_price_positive"),
        Index("ix_executions_trade_id", "trade_id"),
        # No ondelete on the FK, so deleting a Trade that still has executions
        # is refused, not cascaded. Nothing is silently destroyed.
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

    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    # No default: the fill happened when the broker says it happened, which is
    # not when the row was written.
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )