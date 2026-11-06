import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Application-level owner of trading records.

    The spec's users table is "the minimal contract required by the trading
    schema". Authentication is outsourced to Firebase: the ID token's subject
    (the Firebase uid) is kept in `auth_sub` (external IDs are not UUIDs, so they
    cannot be the PK), and the internal `id` stays the FK target for the trading
    tables. Rows are provisioned lazily on first verified request (just-in-time).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # The verified subject: a Firebase uid. Unique, because one Firebase account
    # maps to exactly one journal identity.
    auth_sub: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )

    # The plan this account is on: FREE or PRO. Stored, not derived, so an
    # operator can still comp an account (`scripts/set_plan.py`). A new account
    # starts on FREE (see provision_user), because a plan is bought now; this
    # default is the safety net for a row inserted without the column.
    plan: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'FREE'")
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