import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Application-level owner of trading records.

    The spec's users table is "the minimal contract required by the trading
    schema". Authentication is outsourced to Clerk: the Clerk subject is kept
    in `clerk_sub` (external IDs are not UUIDs, so they cannot be the PK), and
    the internal `id` stays the FK target for the trading tables. Rows are
    provisioned lazily on first verified request (just-in-time).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # Clerk user identifier ("user_..."), unique because one Clerk account maps
    # to exactly one journal identity.
    clerk_sub: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )

    # The plan this account is on: FREE or PRO. Stored, not derived, so a plan
    # can be switched by hand until a payment provider exists. A new account is
    # provisioned as PRO (see provision_user); this default is the safety net
    # for a row inserted without the column.
    plan: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PRO'")
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