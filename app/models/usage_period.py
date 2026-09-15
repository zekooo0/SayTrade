import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UsagePeriod(Base):
    """One billing-accounting period for one user.

    From the monetization spec: the entitlement/accounting mechanism. Journal
    entries remain the historical evidence of recordings; this row is the
    counter with an atomic reserve/refund protocol. The limit is snapshotted
    from configuration when the period is created, so plan changes never
    require a schema migration.

    Lives in its own module, NOT on any trading table, per the spec's rule
    that billing state must not touch the trading domain.
    """

    __tablename__ = "usage_periods"

    __table_args__ = (
        # The spec recommends UNIQUE(user_id, period_start, period_end).
        UniqueConstraint(
            "user_id", "period_start", "period_end",
            name="uq_usage_periods_user_period",
        ),
        Index("ix_usage_periods_user_id", "user_id"),
        # The spec's reservation must be atomic: used can never go below zero
        # or exceed the snapshot limit.
        CheckConstraint(
            "ai_voice_used >= 0 AND ai_voice_used <= ai_voice_limit",
            name="ck_usage_periods_voice_counter",
        ),
        # No ondelete on the FK: a user with usage history cannot be deleted.
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

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Snapshot of the configured plan limit at period creation, so pricing can
    # change without a migration and existing users can be grandfathered.
    ai_voice_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_voice_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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