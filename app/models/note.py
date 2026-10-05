import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

# Pipeline states. The note row is both the journal entry and the pipeline job:
# one row per thing the trader said.
NOTE_STATUSES = ("RECEIVED", "TRANSCRIBED", "EXTRACTED", "APPROVED", "FAILED")
NOTE_SOURCES = ("VOICE", "TEXT", "IMAGE")


class Note(Base):
    """Something the trader said (or typed), plus its processing state.

    ``text`` holds the trader's words: the transcript for a voice note, the
    typed words for a text note. ``summary`` is one AI line for a scannable
    timeline. ``proposal`` is the AI draft under review; the approved result
    lives in the child ``changes`` rows, so no separate approved payload is
    stored.
    """

    __tablename__ = "notes"

    __table_args__ = (
        Index("ix_notes_user_created_at", "user_id", text("created_at DESC")),
        Index("ix_notes_trade_id", "trade_id", "created_at"),
        Index("ix_notes_allowance", "user_id", "source", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # The trade this note belongs to. NULL while a note is still pending, and
    # NULL for a note that opens a new trade until that trade exists.
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trades.id"), nullable=True
    )

    # The market this note was recorded into. The trade it opens lands in the
    # same portfolio, so markets never mix. NULL on notes recorded before
    # portfolios existed.
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("portfolios.id"), nullable=True
    )

    # The trader's words. NULL for a voice note until transcription succeeds.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(10), nullable=False)

    # Media. Each kind keeps its own storage key, and a key is never a URL: the
    # URL is derived when it is needed. `mime_type` and `byte_size` describe
    # whichever key is set.
    audio_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The screenshot the note was read from, when it came from an image.
    image_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    failed_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    transcription_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    transcription_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # The language the transcriber detected. Recorded, not guessed: the summary
    # and the extraction both care, and a wrong guess is how an Egyptian note
    # came back with a Russian ticker.
    transcription_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    extraction_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(60), nullable=True)

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
