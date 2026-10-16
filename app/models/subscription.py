"""The provider subscription behind a paid plan.

One row per subscription LemonSqueezy knows about. The row holds the provider's
ids and the state it reports. The PLAN stays on `users.plan`, and nothing here
decides access.

A trader may hold more than one row over time — cancel, then come back — so the
provider's subscription id is the unique key, not the trader, and the newest row
is the current one.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Subscription(Base):
    """One subscription at the payment provider."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "ls_subscription_id", name="uq_subscriptions_ls_subscription_id"
        ),
        # The portal route reads the newest row for a trader, so the index
        # matches that read.
        Index(
            "ix_subscriptions_user_id_created_at",
            "user_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # The one FK rule of this schema: NO ACTION. A subscription is never
    # cascaded away by a user row.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # The provider's own identifiers. They are integers in its JSON, and a
    # string column cannot run out of digits.
    ls_subscription_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ls_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ls_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # What was bought, and what the provider says its state is.
    #
    # `status` carries no CHECK: the provider owns those values and adds new
    # ones, and an unknown value must still be storable — it maps to FREE in the
    # applier, never to PRO.
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    renews_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # True for a rehearsal purchase, so a test-mode row is visible as one.
    test_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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
