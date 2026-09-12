import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
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

RESULTS = ("WIN", "LOSS", "BREAKEVEN")


class OpportunityOutcome(Base):
    """How an opportunity finished. At most one per opportunity.

    Append-only like the other ledger tables: no updated_at.
    """

    __tablename__ = "opportunity_outcomes"

    __table_args__ = (
        CheckConstraint(
            "type BETWEEN 1 AND 4",
            name="ck_opportunity_outcomes_type",
        ),
        CheckConstraint(
            "result IN ('WIN', 'LOSS', 'BREAKEVEN')",
            name="ck_opportunity_outcomes_result",
        ),
        # One outcome per opportunity. This also creates a unique index, which
        # doubles as the index for the foreign key lookups.
        UniqueConstraint(
            "opportunity_id",
            name="uq_opportunity_outcomes_opportunity_id",
        ),
        # No ondelete on the FK: Postgres defaults apply, so an opportunity that
        # already has an outcome cannot be deleted.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id"),
        nullable=False,
    )

    # Nullable, but when present it must be 1-4. A NULL passes a CHECK
    # automatically, so the range only constrains actual values.
    type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    realized_r_multiple: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    result: Mapped[str] = mapped_column(String(20), nullable=False)

    lesson: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # No default: the trader says when it finished.
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
