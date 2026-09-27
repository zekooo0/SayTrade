"""The note pipeline: receive -> transcribe -> extract -> review -> approve.

One row per thing the trader said. That row is also the job: its status names
the stage, so there is no separate queue table.

A voice note costs one unit of the free allowance, reserved before any AI
spend. A text note costs nothing (it skips transcription).
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.entitlements import Entitlements
from app.domain.errors import (
    NoteNotFoundError,
    StorageUnavailableError,
    TradeNotFoundError,
    WrongNoteStateError,
)
from app.models import Note, Trade, User
from app.providers.extraction import Extractor
from app.providers.storage import AudioStorage
from app.providers.transcription import Transcriber
from app.schemas.api import NoteView
from app.services import notes as note_service
from app.services.notes import (
    STATUS_APPROVED,
    STATUS_EXTRACTED,
    STATUS_FAILED,
    STATUS_RECEIVED,
    STATUS_TRANSCRIBED,
)

SOURCE_VOICE = "VOICE"
SOURCE_TEXT = "TEXT"


def new_storage_key(user_id: UUID, mime_type: str) -> str:
    """Stable key: voice-journal/{user_id}/{random}.

    No file extension (a format argument plus an extension made Cloudinary
    deliver `<key>.webm.webm`, which broke the derived URL) and no note id
    (the key must exist before the row, and it never changes afterwards).
    """
    _ = mime_type
    return f"voice-journal/{user_id}/{uuid4()}"


async def _owned_trade_or_404(
    session: AsyncSession, user: User, trade_id: UUID | None
) -> UUID | None:
    """Recording from a trade page: verify ownership before the note exists."""
    if trade_id is None:
        return None
    trade = await session.scalar(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id)
    )
    if trade is None:
        raise TradeNotFoundError("Trade not found")
    return trade.id


async def receive_voice_note(
    session: AsyncSession,
    user: User,
    data: bytes,
    mime_type: str,
    duration_ms: int,
    storage: AudioStorage,
    entitlements: Entitlements,
    trade_id: UUID | None = None,
    now: datetime | None = None,
) -> NoteView:
    """Reserve one allowance unit, persist the RECEIVED note, store the audio.

    The reservation and the row commit together. The upload happens after the
    commit (a network call outside the row lock); if it fails the note is
    marked FAILED(STORAGE) and the client must re-record.
    """
    now = now or datetime.now(timezone.utc)
    await entitlements.reserve(session, user.id, now)
    owned_trade_id = await _owned_trade_or_404(session, user, trade_id)

    note = Note(
        user_id=user.id,
        trade_id=owned_trade_id,
        source=SOURCE_VOICE,
        status=STATUS_RECEIVED,
        audio_key=new_storage_key(user.id, mime_type),
        mime_type=mime_type,
        duration_ms=duration_ms,
        byte_size=len(data),
    )
    session.add(note)
    await session.commit()

    try:
        assert note.audio_key is not None
        await storage.save(note.audio_key, data, mime_type)
    except Exception as exc:
        note.status = STATUS_FAILED
        note.failed_stage = "STORAGE"
        note.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        await session.commit()
        raise StorageUnavailableError(
            "The recording could not be stored. Record the note again."
        ) from exc

    return await note_service.note_view(session, note, storage)


async def receive_text_note(
    session: AsyncSession,
    user: User,
    text: str,
    trade_id: UUID | None = None,
) -> NoteView:
    """A typed note. No allowance unit: it skips transcription."""
    owned_trade_id = await _owned_trade_or_404(session, user, trade_id)
    note = Note(
        user_id=user.id,
        trade_id=owned_trade_id,
        source=SOURCE_TEXT,
        status=STATUS_RECEIVED,
        text=text,
    )
    session.add(note)
    await session.commit()
    return await note_service.note_view(session, note)


async def _extraction_context(
    session: AsyncSession, note: Note
) -> tuple[str, str] | None:
    """The trade the note was recorded against, as (id, symbol) context.

    The extractor uses the id verbatim, so it never invents one.
    """
    if note.trade_id is None:
        return None
    trade = await session.scalar(
        select(Trade).where(Trade.id == note.trade_id, Trade.user_id == note.user_id)
    )
    if trade is None:
        return None
    return str(trade.id), trade.symbol


async def process_note(
    session_factory: async_sessionmaker[AsyncSession],
    note_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
) -> None:
    """Run the note through transcription (voice only) and extraction.

    Idempotent per stage: a transcribed note is not transcribed again, and an
    extracted note is not extracted again. A failure records the stage that
    broke; a retry resumes from there.
    """
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if note is None or note.status == STATUS_APPROVED:
            return

        try:
            if note.source == SOURCE_VOICE and not note.text:
                if not note.audio_key:
                    raise RuntimeError("the note has no stored audio")
                audio = await storage.read(note.audio_key)
                result = await transcriber.transcribe(
                    audio, note.mime_type or "audio/m4a"
                )
                note.text = result.transcript
                note.transcription_provider = result.provider
                note.transcription_model = result.model
                note.status = STATUS_TRANSCRIBED
                await session.commit()

            if note.status != STATUS_EXTRACTED:
                if not note.text:
                    raise RuntimeError("the note has no text to read")
                extracted = await extractor.extract(
                    note.text,
                    recorded_at=note.created_at,
                    active_trade=await _extraction_context(session, note),
                )
                note.proposal = extracted.proposal
                note.summary = extracted.summary
                note.extraction_provider = extracted.provider
                note.extraction_model = extracted.model
                note.status = STATUS_EXTRACTED
                note.failed_stage = None
                note.error_message = None
                await session.commit()
        except Exception as exc:
            note.status = STATUS_FAILED
            note.failed_stage = (
                "TRANSCRIPTION"
                if note.source == SOURCE_VOICE and not note.text
                else "EXTRACTION"
            )
            note.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            await session.commit()


async def get_note(session: AsyncSession, user: User, note_id: UUID) -> Note:
    note = await session.scalar(
        select(Note).where(Note.id == note_id, Note.user_id == user.id)
    )
    if note is None:
        raise NoteNotFoundError("Note not found")
    return note


async def list_notes(
    session: AsyncSession, user: User, limit: int = 50
) -> list[NoteView]:
    rows = await session.scalars(
        select(Note)
        .where(Note.user_id == user.id)
        .order_by(Note.created_at.desc())
        .limit(limit)
    )
    return [await note_service.note_view(session, note) for note in rows]


async def retry_note(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    user: User,
    note_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
) -> NoteView:
    """Re-run the pipeline from the stalled stage. Never charges twice."""
    note = await get_note(session, user, note_id)
    if note.status == STATUS_APPROVED:
        raise WrongNoteStateError("This note is already approved.")
    if note.status == STATUS_EXTRACTED:
        return await note_service.note_view(session, note, storage)
    if note.failed_stage == "STORAGE":
        raise WrongNoteStateError(
            "The original recording was never stored, so this note cannot be retried. Record it again."
        )
    note.status = STATUS_RECEIVED
    note.failed_stage = None
    note.error_message = None
    await session.commit()
    await process_note(session_factory, note.id, storage, transcriber, extractor)
    await session.refresh(note)
    return await note_service.note_view(session, note, storage)
