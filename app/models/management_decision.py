import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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

DECISION_TYPES = ("STOP_LOSS_CHANGE", "TARGET_CHANGE")


class ManagementDecision(Base):
    """A trade-management decision: what the trader changed and why.

    Append-only like executions: no updated_at, because the record is the
    point. The allowed type vocabulary is API-layer, matching the spec's
    stance on controlled vocabularies.
    """

    __tablename__ = "management_decisions"

    __table_args__ = (
        Index("ix_management_decisions_trade_id", "trade_id"),
        # No ondelete on the FK, so Postgres defaults apply: a Trade that
        # still has decisions attached cannot be deleted.
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

    type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Nullable: old_value is empty the first time a parameter is set
    # (e.g. a stop where none existed before). Both are price-per-share values.
    old_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # No default: the decision happened when the trader made it, which is not
    # necessarily when the row was written.
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )