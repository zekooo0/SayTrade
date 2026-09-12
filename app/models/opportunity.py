import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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
STATUSES = ("IDENTIFIED", "ENTERED", "COMPLETED", "ABANDONED")


class Opportunity(Base):
    """A trade opportunity the trader has identified.

    user_id is intentionally a plain uuid with no foreign key: the users schema
    does not exist yet. Adding the FK later is one ALTER TABLE.
    """

    __tablename__ = "opportunities"

    __table_args__ = (
        CheckConstraint(
            "direction IN ('LONG', 'SHORT')",
            name="ck_opportunities_direction",
        ),
        CheckConstraint(
            "status IN ('IDENTIFIED', 'ENTERED', 'COMPLETED', 'ABANDONED')",
            name="ck_opportunities_status",
        ),
        # No CHECK on pattern: the allowed set has never been defined.
    )

    # uuidv7() is native in PostgreSQL 18. It is time-ordered, so new rows land
    # at the right edge of the index instead of scattering across it the way a
    # random uuidv4 would.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    pattern: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    neckline: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    failure_point: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    # No default: the trader states when the opportunity was identified, which
    # is not necessarily when the row was written.
    identified_at: Mapped[datetime] = mapped_column(
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
