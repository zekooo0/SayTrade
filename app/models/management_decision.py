import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ManagementDecision(Base):
    """A trade-management decision: what the trader changed and why.

    Append-only like executions: no updated_at, because the record is the point.
    `type` is deliberately unconstrained - the allowed set has not been defined,
    so a CHECK here would be guessing at the domain.
    """

    __tablename__ = "management_decisions"

    __table_args__ = (
        # No ondelete on the FK, so Postgres defaults apply: an opportunity that
        # still has decisions attached cannot be deleted.
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

    type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Nullable: old_value is empty the first time something is set, and both are
    # empty if the decision is not numeric (e.g. a status change).
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
