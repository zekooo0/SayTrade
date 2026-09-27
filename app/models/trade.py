import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Trade(Base):
    """One long trade: from the first buy to the full exit.

    Long-only by design: a buy adds shares, a sell trims them. A trade is open
    while ``closed_at`` is NULL — there is no status column to drift out of
    sync. Shares held, average price, realized P&L and R are all derived from
    the change rows (see app/domain/position.py); nothing is cached here.
    """

    __tablename__ = "trades"

    __table_args__ = (
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name="ck_trades_closed_after_open",
        ),
        Index("ix_trades_user_opened_at", "user_id", text("opened_at DESC")),
        # A second open trade in the same symbol is a mistake, not a plan.
        Index(
            "ux_trades_one_open_per_symbol",
            "user_id",
            "symbol",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)

    # "USD", "EGP", ... Empty when the trader never said it. Display only:
    # all money math stays per trade, so no conversion is ever needed.
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The trader states when they entered; not necessarily when the row was written.
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
