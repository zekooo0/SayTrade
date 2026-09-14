import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ENTRY_TYPES = ("VOICE", "TEXT")


class JournalEntry(Base):
    """A journal entry: a voice recording, or something typed.

    user_id is intentionally a plain uuid with no foreign key, matching
    opportunities.user_id: the users schema does not exist yet.
    """

    __tablename__ = "journal_entries"

    __table_args__ = (
        CheckConstraint(
            "type IN ('VOICE', 'TEXT')",
            name="ck_journal_entries_type",
        ),
        # No ondelete on the opportunity FK, so Postgres defaults apply: an
        # opportunity that still has journal entries cannot be deleted.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    # Nullable on purpose: an entry can stand alone as general journaling and
    # need not belong to an opportunity.
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id"),
        nullable=True,
    )

    type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Both nullable and not cross-constrained against `type`: a voice entry whose
    # transcription has not run yet is a legitimate intermediate state.
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
