import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

JOURNAL_TYPES = ("VOICE",)
JOURNAL_STATUSES = ("RECEIVED", "TRANSCRIBED", "EXTRACTED", "APPROVED", "FAILED")


class JournalEntry(Base):
    """The trader's original input (a voice recording in the MVP) plus the
    durable AI-processing state.

    trade_id is NULL while the entry is staged: the first voice note can exist
    before approval creates the Trade. The pipeline (transcribe -> extract ->
    review -> approve) is durable across app kills, network drops and abandoned
    reviews.

    `proposal` (what the AI understood) and `approved_payload` (what the
    trader actually approved) are both kept so the audit trail survives and
    extraction accuracy can be measured later. The AI never writes to domain
    tables; the backend does, from the approved payload.
    """

    __tablename__ = "journal_entries"

    __table_args__ = (
        CheckConstraint("duration_ms > 0", name="ck_journal_entries_duration_positive"),
        CheckConstraint("byte_size > 0", name="ck_journal_entries_byte_size_positive"),
        Index("ix_journal_entries_trade_id", "trade_id"),
        Index("ix_journal_entries_user_id", "user_id"),
        # No ondelete on the FKs, so Postgres defaults apply: a user or Trade
        # that still has journal entries cannot be deleted.
        # The (status, created_at) worker index from the spec is deferred until
        # a polling/queue worker actually exists.
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

    # NULL while the entry is being processed, before a Trade exists.
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("trades.id"),
        nullable=True,
    )

    type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'VOICE'")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Stable storage key, not a URL: URLs change when storage/CDN
    # infrastructure changes, keys do not.
    audio_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)

    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approved_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    transcription_provider: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    transcription_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    failed_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved_at: Mapped[datetime | None] = mapped_column(
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