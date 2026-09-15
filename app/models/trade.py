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

DIRECTIONS = ("LONG", "SHORT")
TRADE_STATUSES = ("ACTIVE", "COMPLETED")


class Trade(Base):
    """A trading opportunity the trader actually entered.

    Created only when the user approves a voice-note proposal. BUY/SELL fills,
    management decisions, journal entries and the outcome all hang off it.
    One Trade covers scale-ins, partial exits and re-entries until the trader
    declares it finished.
    """

    __tablename__ = "trades"

    __table_args__ = (
        # The only DB constraint the spec mandates for trades:
        #   status = COMPLETED  <->  completed_at IS NOT NULL
        # The column goes NULL when status is ACTIVE, and must be set at
        # completion. Value vocabularies (direction, pattern, status) are
        # deliberately API-layer, per the spec's pattern section.
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="ck_trades_status_completed_at",
        ),
        # No ondelete on the FKs: Postgres defaults apply, so a user with
        # trading history cannot be deleted. Nothing is silently destroyed.
        Index("ix_trades_user_id_status", "user_id", "status"),
        Index("ix_trades_user_id_opened_at", "user_id", text("opened_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    pattern: Mapped[str] = mapped_column(String(50), nullable=False)

    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_point: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    target_price: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # No default: the trader states when they entered, which is not
    # necessarily when the row was written.
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
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