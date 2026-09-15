"""Journal pipeline: receive -> (background) transcribe -> extract -> review -> approve.

Durability rules from the spec:
  - The RECEIVED row is written (with the allowance reservation) before any
    expensive processing; the audio lives behind a stable storage key.
  - A processing failure sets FAILED + failed_stage + a safe diagnostic and
    refunds the allowance unit; nothing is lost and the same entry retries.
  - Retrying never reserves another unit and never duplicates the entry.
"""

import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import (
    JournalEntryNotFoundError,
    StorageUnavailableError,
    TradeNotFoundError,
    WrongJournalStateError,
)
from app.billing.entitlements import Entitlements
from app.models import JournalEntry, Trade, User
from app.providers.extraction import Extractor
from app.providers.storage import AudioStorage
from app.providers.transcription import Transcriber
from app.schemas.api import JournalEntryView

STATUS_RECEIVED = "RECEIVED"
STATUS_TRANSCRIBED = "TRANSCRIBED"
STATUS_EXTRACTED = "EXTRACTED"
STATUS_APPROVED = "APPROVED"
STATUS_FAILED = "FAILED"

STAGE_STORAGE = "STORAGE"
STAGE_TRANSCRIPTION = "TRANSCRIPTION"
STAGE_EXTRACTION = "EXTRACTION"


def _safe_message(exc: Exception, limit: int = 500) -> str:
    # Diagnostics are stored for debugging; keep them bounded, never secrets.
    import traceback

    return traceback.format_exception_only(type(exc), exc)[-1].strip()[:limit]


def new_storage_key(user_id: UUID, mime_type: str) -> str:
    """Stable key, namespaced per the spec's example.

    Deliberately has NO file extension: the extension in the key AND a format
    argument on upload made Cloudinary deliver `<key>.webm.webm`, so the URL
    derived from the key 404'd. The format lives in the asset, not the key.
    Deliberately does NOT embed the journal entry id either: the key must be
    known before the row exists and must never change afterwards.
    """
    return f"voice-journal/{user_id}/{uuid.uuid4()}"


async def receive_journal(
    session: AsyncSession,
    user: User,
    data: bytes,
    mime_type: str,
    duration_ms: int,
    storage: AudioStorage,
    entitlements: Entitlements,
    trade_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> JournalEntryView:
    """Reserve one allowance unit, persist the RECEIVED row, store the audio.

    The reservation and the row commit together (atomic). The upload happens
    after the commit (network call outside the row lock); if it fails the
    entry is marked FAILED(STORAGE) and the unit is refunded — retry cannot
    recover an upload that never landed, so the client must re-record.

    `trade_id` is the "recording from an active Trade screen" context (spec:
    resolving later notes). Ownership is verified here, and the id is stored
    on the entry so the extractor (and the retry path) knows which Trade the
    note refers to — the LLM never guesses ids.
    """
    now = now or datetime.now(timezone.utc)
    if trade_id is not None:
        owned = await session.scalar(
            select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id)
        )
        if owned is None:
            raise TradeNotFoundError("Trade not found")
    key = new_storage_key(user.id, mime_type)
    entry = JournalEntry(
        user_id=user.id,
        status=STATUS_RECEIVED,
        trade_id=trade_id,
        audio_storage_key=key,
        mime_type=mime_type,
        duration_ms=duration_ms,
        byte_size=len(data),
    )
    await entitlements.reserve(session, user.id, now)
    session.add(entry)
    await session.commit()  # reservation + row are one atomic unit

    try:
        await storage.save(key, data, mime_type)
    except Exception as exc:
        await _mark_failed(
            session, entry.id, STAGE_STORAGE, exc, user.id, entitlements
        )
        raise StorageUnavailableError(f"audio storage failed: {_safe_message(exc)}") from exc

    return await journal_view(session, user.id, entry.id, storage)


async def _mark_failed(
    session: AsyncSession,
    entry_id: UUID,
    stage: str,
    exc: Exception,
    user_id: UUID,
    entitlements: Entitlements,
) -> None:
    entry = await session.get(JournalEntry, entry_id)
    if entry is None or entry.status == STATUS_APPROVED:
        return
    entry.status = STATUS_FAILED
    entry.failed_stage = stage
    entry.error_message = _safe_message(exc)
    await entitlements.refund(session, user_id)
    await session.commit()


async def _extraction_context(
    session: AsyncSession, entry: JournalEntry
) -> tuple[str, str] | None:
    """The Trade the note was recorded against (from the active-Trade screen),
    as (id, symbol) context for the extractor — the LLM uses it verbatim and
    never invents ids."""
    if entry.trade_id is None:
        return None
    trade = await session.scalar(
        select(Trade).where(Trade.id == entry.trade_id, Trade.user_id == entry.user_id)
    )
    if trade is None:
        return None
    return str(trade.id), trade.symbol


async def process_entry(
    entry_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
    entitlements: Entitlements,
    session_factory: async_sessionmaker,
) -> None:
    """Background pipeline: transcribe (if missing) then extract.

    Each stage commits as it completes, so progress survives crashes; the
    retry endpoint resumes from the stalled stage. Any failure marks FAILED
    and refunds the allowance unit.
    """
    async with session_factory() as session:
        entry = await session.get(JournalEntry, entry_id)
        if entry is None or entry.status in (STATUS_APPROVED, STATUS_FAILED):
            return
        try:
            if entry.transcript is None:
                audio = await storage.read(entry.audio_storage_key)
                result = await transcriber.transcribe(audio, entry.mime_type)
                entry.transcript = result.transcript
                entry.transcription_provider = result.provider
                entry.transcription_model = result.model
                entry.status = STATUS_TRANSCRIBED
                await session.commit()

            if entry.status != STATUS_EXTRACTED:
                result = await extractor.extract(
                    entry.transcript,
                    recorded_at=entry.created_at,
                    active_trade=await _extraction_context(session, entry),
                )
                entry.proposal = {"actions": result.actions}
                entry.extraction_provider = result.provider
                entry.extraction_model = result.model
                entry.status = STATUS_EXTRACTED
                await session.commit()
        except Exception as exc:
            stage = (
                STAGE_TRANSCRIPTION if entry.transcript is None else STAGE_EXTRACTION
            )
            await _mark_failed(
                session, entry.id, stage, exc, entry.user_id, entitlements
            )


async def retry_journal(
    session: AsyncSession,
    user: User,
    entry_id: UUID,
    storage: AudioStorage,
    transcriber: Transcriber,
    extractor: Extractor,
    entitlements: Entitlements,
) -> JournalEntryView:
    """Re-run the pipeline from the stalled stage. Idempotent: EXTRACTED is a
    no-op, APPROVED is refused, and retries never reserve another unit."""
    entry = await session.scalar(
        select(JournalEntry)
        .where(JournalEntry.id == entry_id, JournalEntry.user_id == user.id)
        .with_for_update()
    )
    if entry is None:
        raise JournalEntryNotFoundError("Journal entry not found")
    if entry.status == STATUS_APPROVED:
        raise WrongJournalStateError("Journal entry is already APPROVED")
    if entry.status == STATUS_EXTRACTED:
        return await journal_view(session, user.id, entry.id, storage)
    if entry.failed_stage == STAGE_STORAGE:
        raise WrongJournalStateError(
            "the original recording was never persisted; please record again"
        )

    try:
        if entry.transcript is None:
            audio = await storage.read(entry.audio_storage_key)
            result = await transcriber.transcribe(audio, entry.mime_type)
            entry.transcript = result.transcript
            entry.transcription_provider = result.provider
            entry.transcription_model = result.model
            entry.status = STATUS_TRANSCRIBED
            await session.commit()

        if entry.status != STATUS_EXTRACTED:
            result = await extractor.extract(
                entry.transcript,
                recorded_at=entry.created_at,
                active_trade=await _extraction_context(session, entry),
            )
            entry.proposal = {"actions": result.actions}
            entry.extraction_provider = result.provider
            entry.extraction_model = result.model
            entry.status = STATUS_EXTRACTED
            await session.commit()
    except Exception as exc:
        stage = STAGE_TRANSCRIPTION if entry.transcript is None else STAGE_EXTRACTION
        await _mark_failed(session, entry.id, stage, exc, user.id, entitlements)
        raise
    return await journal_view(session, user.id, entry.id, storage)


async def journal_view(
    session: AsyncSession,
    user_id: UUID,
    entry_id: UUID,
    storage: AudioStorage,
) -> JournalEntryView:
    """Serialize one entry (owned) with its derived audio URL."""
    entry = await session.scalar(
        select(JournalEntry).where(
            JournalEntry.id == entry_id, JournalEntry.user_id == user_id
        )
    )
    if entry is None:
        raise JournalEntryNotFoundError("Journal entry not found")
    try:
        audio_url = await storage.url(entry.audio_storage_key)
    except Exception:
        audio_url = None
    return JournalEntryView(
        id=entry.id,
        status=entry.status,
        type=entry.type,
        trade_id=entry.trade_id,
        transcript=entry.transcript,
        proposal=entry.proposal,
        approved_payload=entry.approved_payload,
        audio_url=audio_url,
        transcription_provider=entry.transcription_provider,
        transcription_model=entry.transcription_model,
        extraction_provider=entry.extraction_provider,
        extraction_model=entry.extraction_model,
        failed_stage=entry.failed_stage,
        error_message=entry.error_message,
        approved_at=entry.approved_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )