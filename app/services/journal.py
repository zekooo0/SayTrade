"""The note pipeline: receive -> transcribe -> extract -> review -> approve.

One row per thing the trader said. That row is also the job: its status names
the stage, so there is no separate queue table.

A voice note counts against the plan's monthly AI voice guard, checked before
any AI spend. A text note costs nothing (it skips transcription).

What a plan really sells is active trades, and that limit is enforced at
approval time — in the applier, where a trade is actually created.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
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
from app.services import summary as summary_service
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
    await entitlements.reserve_voice(session, user, now)
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


async def _staged_write(session: AsyncSession, note_id: UUID, **values) -> bool:
    """Write a stage result, but only if the note is still there.

    The trader can discard a note while the AI is still reading it. Deleting the
    row is the trader's decision, so the pipeline must stop quietly instead of
    writing to something that no longer exists. The UPDATE is its own test: no
    matched row means the note is gone, and that is not an error.

    The write goes through the Core statement rather than through the ORM object,
    because a unit-of-work flush against a deleted row raises StaleDataError.
    """
    updated = await session.scalar(
        update(Note).where(Note.id == note_id).values(**values).returning(Note.id)
    )
    if updated is None:
        await session.rollback()
        return False
    await session.commit()
    return True


async def process_note(
    session_factory: async_sessionmaker[AsyncSession],
    note_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
    summarizer: summary_service.Summarizer | None = None,
) -> None:
    """Run the note through transcription (voice only) and extraction.

    Idempotent per stage: a transcribed note is not transcribed again, and an
    extracted note is not extracted again. A failure records the stage that
    broke; a retry resumes from there.

    A discarded note ends the run without a word: the row is gone, so every
    write below is a no-op.
    """
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if note is None or note.status == STATUS_APPROVED:
            return

        # Read the note once, into locals. Nothing below assigns to the ORM
        # object, so it never becomes dirty and no flush can fight the discard.
        source = note.source
        text = note.text
        audio_key = note.audio_key
        mime_type = note.mime_type
        status = note.status

        try:
            if source == SOURCE_VOICE and not text:
                if not audio_key:
                    raise RuntimeError("the note has no stored audio")
                audio = await storage.read(audio_key)
                result = await transcriber.transcribe(
                    audio, mime_type or "audio/m4a"
                )
                text = result.transcript
                status = STATUS_TRANSCRIBED
                if not await _staged_write(
                    session,
                    note_id,
                    text=text,
                    transcription_provider=result.provider,
                    transcription_model=result.model,
                    status=status,
                ):
                    return

            if status != STATUS_EXTRACTED:
                if not text:
                    raise RuntimeError("the note has no text to read")
                extracted = await extractor.extract(
                    text,
                    recorded_at=note.created_at,
                    active_trade=await _extraction_context(session, note),
                )
                # The summary is written from the changes, never from the
                # transcript, so it cannot repeat a mishearing. Written here so
                # the review screen has the line, and written again at approval
                # from the rows that are actually applied.
                proposal = extracted.proposal or {}
                summary = await summary_service.write_summary(
                    proposal.get("changes") or [],
                    symbol=proposal.get("symbol"),
                    language=summary_service.language_of(
                        proposal.get("language"), text
                    ),
                    summarizer=summarizer,
                    text=text,
                )
                await _staged_write(
                    session,
                    note_id,
                    proposal=extracted.proposal,
                    summary=summary,
                    extraction_provider=extracted.provider,
                    extraction_model=extracted.model,
                    status=STATUS_EXTRACTED,
                    failed_stage=None,
                    error_message=None,
                )
        except Exception as exc:
            await _staged_write(
                session,
                note_id,
                status=STATUS_FAILED,
                failed_stage=(
                    "TRANSCRIPTION"
                    if source == SOURCE_VOICE and not text
                    else "EXTRACTION"
                ),
                error_message=f"{type(exc).__name__}: {exc}"[:2000],
            )


async def _note_exists(session: AsyncSession, note_id: UUID) -> bool:
    """Is the row still there?

    A column-only select asks the database. `session.get()` would answer from
    this session's identity map, which still holds the object after a discard
    that happened in another session.
    """
    return (await session.scalar(select(Note.id).where(Note.id == note_id))) is not None


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


async def discard_note(
    session: AsyncSession,
    user: User,
    note_id: UUID,
    storage: AudioStorage,
) -> None:
    """Throw a note away for good: the row, its audio, and the AI reading.

    Only a note the trader never approved can be discarded. Once a note has
    changed a position, its change rows are the trade's history and they are not
    deleted from under it — the trade page still shows what was decided, and a
    reversal is a new note.

    The recording goes with it: a discarded note must not leave audio behind in
    storage. The row lock is deliberate, so a discard racing an approval cannot
    delete a note whose changes were just applied.
    """
    note = await session.scalar(
        select(Note)
        .where(Note.id == note_id, Note.user_id == user.id)
        .with_for_update()
    )
    if note is None:
        raise NoteNotFoundError("No such note.")

    if note.status == STATUS_APPROVED:
        raise WrongNoteStateError(
            "This note is approved, so it is part of the trade's history and "
            "cannot be discarded. Record a new note to change the position."
        )

    if note.audio_key:
        try:
            await storage.delete(note.audio_key)
        except Exception:
            # The trader asked for the note to be gone; the row is the copy that
            # matters, so a storage failure must not keep it alive.
            pass

    await session.delete(note)


async def retry_note(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    user: User,
    note_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
    summarizer: summary_service.Summarizer | None = None,
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
    await process_note(
        session_factory, note.id, storage, transcriber, extractor, summarizer
    )
    if not await _note_exists(session, note.id):
        # The trader discarded the note while this retry was running, so there
        # is nothing left to refresh or to show.
        raise NoteNotFoundError("This note was discarded while it was being read.")
    await session.refresh(note)
    return await note_service.note_view(session, note, storage)
