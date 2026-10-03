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
    MarketRequiredError,
    PortfolioNotFoundError,
    NoteNotFoundError,
    StorageUnavailableError,
    TradeNotFoundError,
    WrongNoteStateError,
)
from app.models import Note, Portfolio, Prediction, Trade, User
from app.providers.extraction import (
    Extractor,
    MarketContext,
    TradeContext,
)
from app.providers.storage import AudioStorage
from app.providers.transcription import Transcriber, transcription_keywords
from app.schemas.api import NoteView
from app.log import get_logger
from app.services import notes as note_service
from app.services import summary as summary_service
from app.services import symbols as symbols_service
from app.services.notes import (
    STATUS_APPROVED,
    STATUS_EXTRACTED,
    STATUS_FAILED,
    STATUS_RECEIVED,
    STATUS_TRANSCRIBED,
)

SOURCE_VOICE = "VOICE"
SOURCE_TEXT = "TEXT"

log = get_logger("saytrade.journal")


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


def _require_a_home(trade_id: UUID | None, portfolio_id: UUID | None) -> None:
    """A note must have somewhere to go: a trade to follow up on, or a market to
    record into.

    Without either, the trade the note opens would belong to no market, and
    markets never mix — there is nothing sensible to do with it. The trader
    creates a market first; the app does not invent one.
    """
    if trade_id is None and portfolio_id is None:
        raise MarketRequiredError(
            "Create a market first, or record the note from a trade page."
        )


async def _owned_portfolio_or_404(
    session: AsyncSession, user: User, portfolio_id: UUID | None
) -> UUID | None:
    """The portfolio must exist and belong to this trader.

    A guessed id is a 404, never a silent NULL: the note would otherwise land in
    no market, and the trade it opens would mix with another market's.
    """
    if portfolio_id is None:
        return None
    portfolio = await session.scalar(
        select(Portfolio).where(
            Portfolio.id == portfolio_id, Portfolio.user_id == user.id
        )
    )
    if portfolio is None:
        raise PortfolioNotFoundError("Portfolio not found")
    return portfolio.id


async def receive_voice_note(
    session: AsyncSession,
    user: User,
    data: bytes,
    mime_type: str,
    duration_ms: int,
    storage: AudioStorage,
    entitlements: Entitlements,
    trade_id: UUID | None = None,
    portfolio_id: UUID | None = None,
    now: datetime | None = None,
) -> NoteView:
    """Reserve one allowance unit, persist the RECEIVED note, store the audio.

    The reservation and the row commit together. The upload happens after the
    commit (a network call outside the row lock); if it fails the note is
    marked FAILED(STORAGE) and the client must re-record.
    """
    now = now or datetime.now(timezone.utc)
    # Before the allowance is spent: a note that cannot go anywhere must not cost
    # the trader a voice unit.
    _require_a_home(trade_id, portfolio_id)
    await entitlements.reserve_voice(session, user, now)
    owned_trade_id = await _owned_trade_or_404(session, user, trade_id)
    owned_portfolio_id = await _owned_portfolio_or_404(session, user, portfolio_id)

    note = Note(
        user_id=user.id,
        trade_id=owned_trade_id,
        portfolio_id=owned_portfolio_id,
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
    portfolio_id: UUID | None = None,
) -> NoteView:
    """A typed note. No allowance unit: it skips transcription."""
    _require_a_home(trade_id, portfolio_id)
    owned_trade_id = await _owned_trade_or_404(session, user, trade_id)
    owned_portfolio_id = await _owned_portfolio_or_404(session, user, portfolio_id)
    note = Note(
        user_id=user.id,
        trade_id=owned_trade_id,
        portfolio_id=owned_portfolio_id,
        source=SOURCE_TEXT,
        status=STATUS_RECEIVED,
        text=text,
    )
    session.add(note)
    await session.commit()
    return await note_service.note_view(session, note)


async def _extraction_context(
    session: AsyncSession, note: Note
) -> TradeContext | None:
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
    return TradeContext(
        id=str(trade.id),
        symbol=trade.symbol,
        closed=trade.closed_at is not None,
        pending_prediction=await _pending_prediction(session, trade),
    )


async def _pending_prediction(session: AsyncSession, trade: Trade) -> str | None:
    """A one-line description of the trade's unresolved prediction, if it has one.

    Handed to the extractor so it can recognise a later note on this trade as the
    resolution of that prediction, rather than a note about nothing.
    """
    prediction = await session.scalar(
        select(Prediction).where(
            Prediction.trade_id == trade.id, Prediction.result == "PENDING"
        )
    )
    if prediction is None:
        return None
    if prediction.level is None:
        return "the trade's failure point"
    direction = f", {prediction.direction.lower()}" if prediction.direction else ""
    return f"price {prediction.level}{direction}"


async def _market_context(session: AsyncSession, note: Note) -> MarketContext | None:
    """The market a note was recorded into, with the tickers held there.

    None for a follow-up: it has no market of its own, and the trade it belongs
    to is already in the extractor's context.
    """
    if note.portfolio_id is None:
        return None
    portfolio = await session.get(Portfolio, note.portfolio_id)
    if portfolio is None:
        return None
    symbols = list(
        await session.scalars(
            select(Trade.symbol)
            .where(Trade.portfolio_id == portfolio.id, Trade.closed_at.is_(None))
            .distinct()
        )
    )
    return MarketContext(
        name=portfolio.name,
        currency=portfolio.currency,
        symbols=tuple(symbols),
        exchange=portfolio.exchange,
    )


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
                # The decoder hints: the tickers this market holds, and the two
                # languages the trader records in. Without them the model reads
                # Egyptian colloquial Arabic badly and loses the numbers.
                market = await _market_context(session, note)
                result = await transcriber.transcribe(
                    audio,
                    mime_type or "audio/m4a",
                    keywords=transcription_keywords(market.symbols if market else ()),
                    languages=("ar", "en"),
                )
                text = result.transcript
                status = STATUS_TRANSCRIBED
                log.info("note %s: transcribed, %d characters", note_id, len(text))
                if not await _staged_write(
                    session,
                    note_id,
                    text=text,
                    transcription_provider=result.provider,
                    transcription_model=result.model,
                    transcription_language=result.language,
                    status=status,
                ):
                    return

            if status != STATUS_EXTRACTED:
                if not text:
                    raise RuntimeError("the note has no text to read")
                market = await _market_context(session, note)
                log.info(
                    "note %s: extracting | market=%s | text=%r",
                    note_id,
                    market.name if market else None,
                    text,
                )
                extracted = await extractor.extract(
                    text,
                    recorded_at=note.created_at,
                    active_trade=await _extraction_context(session, note),
                    market=market,
                )
                # The summary is written from the changes, never from the
                # transcript, so it cannot repeat a mishearing. Written here so
                # the review screen has the line, and written again at approval
                # from the rows that are actually applied.
                proposal = extracted.proposal or {}
                # The symbol in that line is the one the market's own list
                # confirms, never the model's word for it.
                found = await symbols_service.resolve_candidates(
                    session,
                    market.exchange if market else None,
                    proposal.get("security_candidates") or [],
                )
                summary = await summary_service.write_summary(
                    proposal.get("changes") or [],
                    symbol=found[0].ticker if found else None,
                    language=summary_service.language_of(
                        proposal.get("language"), text
                    ),
                    summarizer=summarizer,
                    text=text,
                )
                log.info("note %s: summary -> %r", note_id, summary)
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
