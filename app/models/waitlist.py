import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WaitlistSignup(Base):
    """One person who asked to be told when the app reaches the store.

    Deliberately NOT a user: there is no account, no plan, no journal and no
    subject. The only fact stored is the address they typed, so a marketing list
    can be deleted without touching anybody's trades.
    """

    __tablename__ = "waitlist"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # Stored lowercased, so "A@b.com" and "a@b.com" are one person. The unique
    # constraint is what makes a double submit harmless.
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)

    # Where the address came from ("landing", later "pricing"), so a list can be
    # split by the page that earned it without a second table.
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'landing'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
