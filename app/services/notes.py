"""The note applier: turns one reviewed note into approved change rows.

One engine, one transaction. The rules:

* The AI proposes; the trader approves. Nothing changes a position before an
  approval.
* An approval is atomic and idempotent: the note status is the commit marker,
  and a second approval of an APPROVED note replays the stored result instead
  of applying twice.
* Every value the change needs must be present. A missing value names itself
  in the error so the trader can fix it in one tap.
* Long only: a sale can never exceed the shares held.
* The plan's active-trade limit is checked here, at the one place a trade is
  created, and only for a note that opens a new trade.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.entitlements import Entitlements
from app.config import settings
from app.domain import position as pos
from app.domain.errors import (
    AmbiguousTradeError,
    IncompleteNoteError,
    PositionViolationError,
    TradeClosedError,
    TradeNotFoundError,
    WrongNoteStateError,
)
from app.models import Change, Note, Portfolio, Prediction, Trade, User
from app.log import get_logger
from app.schemas.api import ApproveResultView, ChangeView, NoteView, TradeSummaryView, d
from app.schemas.proposal import ChangeProposal, NoteProposal
from app.services import summary as summary_service
from app.services import symbols as symbols_service

STATUS_RECEIVED = "RECEIVED"
STATUS_TRANSCRIBED = "TRANSCRIBED"
STATUS_EXTRACTED = "EXTRACTED"
STATUS_APPROVED = "APPROVED"
STATUS_FAILED = "FAILED"

log = get_logger("saytrade.notes")


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def change_view(change: Change) -> ChangeView:
    return ChangeView(
        id=change.id,
        kind=change.kind,
        occurred_at=change.occurred_at,
        shares=d(change.shares),
        price=d(change.price),
        stop=d(change.stop),
        target=d(change.target),
    )


async def changes_for_note(session: AsyncSession, note_id: UUID) -> list[Change]:
    rows = await session.scalars(
        select(Change)
        .where(Change.note_id == note_id)
        .order_by(Change.occurred_at, Change.id)
    )
    return list(rows)


async def changes_for_trade(session: AsyncSession, trade_id: UUID) -> list[Change]:
    """Every approved change on a trade, in market-time order."""
    rows = await session.scalars(
        select(Change)
        .join(Note, Change.note_id == Note.id)
        .where(Note.trade_id == trade_id)
        .order_by(Change.occurred_at, Change.id)
    )
    return list(rows)


async def notes_for_trade(
    session: AsyncSession, trade_id: UUID, storage=None
) -> list[NoteView]:
    rows = await session.scalars(
        select(Note).where(Note.trade_id == trade_id).order_by(Note.created_at)
    )
    return [await note_view(session, note, storage) for note in rows]


@dataclass
class TradeResolution:
    """Which trade a note belongs to, and what to ask when that is not known.

    ``trade`` is the answer when there is one. ``candidates`` holds the trader's
    open trades when a note that changes a position does not say which one it
    means: the view offers them and the applier refuses without one of them, so
    the screen can never show a trade the applier would not use.
    """

    trade: Trade | None = None
    candidates: list[Trade] = field(default_factory=list)
    trade_id_missing: bool = False


async def held_symbols(session: AsyncSession, note: Note) -> list[str]:
    """The tickers this note's market already holds.

    The review screen warns when the model read a ticker that is not among them.
    A ticker from another market is the one mistake a trader may not catch — his
    Egyptian note came back as MTSS, a Russian telecom, because the market held
    nothing to match his words against.
    """
    if note.portfolio_id is None:
        return []
    rows = await session.scalars(
        select(Trade.symbol)
        .where(Trade.portfolio_id == note.portfolio_id)
        .distinct()
    )
    return sorted(set(rows))


async def read_symbols(session: AsyncSession, note: Note) -> list[str]:
    """The tickers the note's own words point at, verified against the market.

    The model proposes companies; the symbols table decides which of them trade
    in this note's market. A candidate whose ticker is not on that market's list
    is dropped, and its name is looked up instead — which is how an Arabic
    company name becomes a ticker, since the table holds no Arabic.

    Nothing the model says is taken on its word. This is what stops a misheard
    word becoming a confident, wrong ticker in the trader's journal.
    """
    proposal = note.proposal if isinstance(note.proposal, dict) else {}
    candidates = proposal.get("security_candidates") or []
    if not candidates:
        # Notes read before candidates existed carried a bare symbol.
        legacy = proposal.get("symbol")
        if isinstance(legacy, str) and legacy.strip():
            return [legacy.strip().upper()]
        return []
    if note.portfolio_id is None:
        return []
    exchange = await session.scalar(
        select(Portfolio.exchange).where(Portfolio.id == note.portfolio_id)
    )
    found = await symbols_service.resolve_candidates(session, exchange, candidates)
    return [symbol.ticker for symbol in found]


async def _record_prediction(
    session: AsyncSession,
    user: User,
    note: Note,
    trade: Trade,
    proposed,
) -> Prediction | None:
    """One row per closed trade: what the trader expected to happen next.

    Only for a trade this note actually closed. The level defaults to the trade's
    stop, which is the failure point the trader is describing — he names one only
    when it differs. A trade with no stop and no named level has nothing to judge,
    so it gets no prediction.
    """
    if trade.closed_at is None:
        return None
    existing = await session.scalar(
        select(Prediction).where(Prediction.trade_id == trade.id)
    )
    if existing is not None:
        return existing

    changes = await changes_for_trade(session, trade.id)
    level = proposed.level or pos.current_stop(changes) or pos.initial_stop(changes)
    if level is None:
        log.info("note %s: no failure point to predict against, no prediction", note.id)
        return None

    direction = proposed.direction
    if direction is None:
        entry = pos.average_price(changes)
        if entry is not None:
            direction = "BELOW" if Decimal(str(level)) < entry else "ABOVE"

    prediction = Prediction(
        user_id=user.id,
        trade_id=trade.id,
        note_id=note.id,
        level=level,
        direction=direction,
        reason=proposed.reason,
        result="PENDING",
    )
    session.add(prediction)
    await session.flush()
    log.info(
        "note %s: prediction on trade %s | level=%s %s | reason=%r",
        note.id,
        trade.id,
        level,
        direction,
        proposed.reason,
    )
    return prediction


async def _resolve_prediction(
    session: AsyncSession, trade: Trade, note: Note, proposed
) -> None:
    """A later note says whether the predicted move happened.

    The trade is closed and the note carries its id, so there is nothing to
    identify: the only pending prediction on this trade is the one being judged.
    """
    if proposed.outcome is None or trade.closed_at is None:
        return
    prediction = await session.scalar(
        select(Prediction).where(
            Prediction.trade_id == trade.id, Prediction.result == "PENDING"
        )
    )
    if prediction is None:
        log.info(
            "note %s: a resolution was read, but trade %s has nothing pending",
            note.id,
            trade.id,
        )
        return
    prediction.result = proposed.outcome
    prediction.resolved_at = datetime.now(timezone.utc)
    prediction.resolved_by_note_id = note.id
    await session.flush()
    log.info(
        "note %s: prediction on trade %s resolved %s (%r)",
        note.id,
        trade.id,
        proposed.outcome,
        proposed.note,
    )


async def next_trade_number(session: AsyncSession, user_id: UUID) -> int:
    """The trader's next readable trade number: #1, #2, #3.

    Read inside the transaction that creates the trade. A race would collide on
    the (user_id, number) unique constraint, which is the guard — and a trader
    only ever races with himself.
    """
    highest = await session.scalar(
        select(func.max(Trade.number)).where(Trade.user_id == user_id)
    )
    return (highest or 0) + 1


def _same_market(query, note: Note):
    """Restrict a trade lookup to the note's own market.

    Markets never mix, so a symbol match or an "only open trade" answer must come
    from the note's own portfolio. Without this, a note recorded in Egypt could
    resolve to a US trade — and the wrong trade would take the change.
    """
    if note.portfolio_id is None:
        return query
    return query.where(Trade.portfolio_id == note.portfolio_id)


async def resolve_note_trade(
    session: AsyncSession,
    user_id: UUID,
    note: Note,
    symbols: list[str],
    needs_trade: bool,
    lock: bool = False,
) -> TradeResolution:
    """The one answer to "which trade is this note about?".

    Every step is a fact rather than a guess:

    1. The trade the note was recorded against, or the one its changes were
       applied to (the applier stamps ``note.trade_id``).
    2. An open trade in one of the symbols the trader named — already verified
       against the market's own list by ``read_symbols``. A symbol the model read
       is a CANDIDATE: it resolves a trade only by matching one the trader
       already holds. A symbol that matches nothing is not a trade reference — a
       trade is never invented from it. Two matches are ambiguous, and the
       candidates come back for the caller to ask about.
    3. The only open trade, when the note changes a position but names no trade
       (it was recorded from the journal, so the trader never said which one).
       With two or more open trades the answer is genuinely unknown, and the
       candidates come back for the caller to ask about.
    4. The open trades as candidates, when the note changes nothing and names no
       trade. Nothing is assumed for it — but the trader may still want the note
       filed under a trade, so the review screen can offer them.

    ``lock`` takes the row lock the applier needs; the read path leaves it off.
    """
    if note.trade_id is not None:
        query = select(Trade).where(Trade.id == note.trade_id, Trade.user_id == user_id)
        if lock:
            query = query.with_for_update()
        trade = await session.scalar(query)
        if trade is not None:
            return TradeResolution(trade=trade)
        # A stamped trade that is not there is a data problem, not a silence.
        return TradeResolution(trade_id_missing=True)

    if symbols:
        query = select(Trade).where(
            Trade.user_id == user_id,
            Trade.symbol.in_(symbols),
            Trade.closed_at.is_(None),
        )
        query = _same_market(query, note)
        if lock:
            query = query.with_for_update()
        matched = list(await session.scalars(query))
        if len(matched) == 1:
            return TradeResolution(trade=matched[0])

    query = (
        select(Trade)
        .where(Trade.user_id == user_id, Trade.closed_at.is_(None))
        .order_by(Trade.opened_at.desc())
    )
    query = _same_market(query, note)
    if lock:
        query = query.with_for_update()
    open_trades = list(await session.scalars(query))

    if not needs_trade and not symbols:
        # Nothing to apply and nothing named: the note is not filed on its own,
        # but the open trades still come back so the review screen can offer to
        # file it. An empty answer here is what left the Symbol blank with
        # nothing to pick — the trader could see the problem and not fix it.
        return TradeResolution(candidates=open_trades)

    if needs_trade and len(open_trades) == 1:
        # Only a note that CHANGES an existing position may have a trade assumed
        # for it. A note that OPENS one must never be pointed at a trade the
        # trader already holds: in a market with exactly one trade, that would
        # silently dress a new position in the old trade's symbol.
        return TradeResolution(trade=open_trades[0])
    return TradeResolution(candidates=open_trades)


async def symbol_view(session: AsyncSession, note: Note) -> tuple[str | None, list[str]]:
    """The symbol to show for a note, and the trades to choose from.

    The trade wins, always: a note that belongs to a trade wears that trade's
    symbol, whatever the model read in the trader's words. The model's own
    reading is shown only where there is no trade to inherit from — a note that
    opens one, or an observation that changes nothing. When a position-changing
    note has no trade and the trader holds more than one, the symbol is
    genuinely unknown: the answer is None and the open trades come back as the
    choices for the review screen to offer.
    """
    proposal = note.proposal if isinstance(note.proposal, dict) else None
    changes = (proposal or {}).get("changes") or []
    opens = any(c.get("kind") == "OPEN" for c in changes)
    read = await read_symbols(session, note)

    resolution = await resolve_note_trade(
        session, note.user_id, note, read, needs_trade=bool(changes) and not opens
    )
    if resolution.trade is not None:
        return resolution.trade.symbol, []
    if read and (not changes or opens):
        return read[0], []
    return None, [t.symbol for t in resolution.candidates]


async def note_symbol(session: AsyncSession, note: Note) -> str | None:
    """The symbol a note belongs to. See ``symbol_view``."""
    symbol, _candidates = await symbol_view(session, note)
    return symbol


async def note_view(session: AsyncSession, note: Note, storage=None) -> NoteView:
    changes = await changes_for_note(session, note.id)
    symbol, candidates = await symbol_view(session, note)
    audio_url = None
    if note.audio_key and storage is not None:
        try:
            audio_url = await storage.delivery_url(note.audio_key)
        except Exception:
            audio_url = None
    return NoteView(
        id=note.id,
        status=note.status,
        source=note.source,
        trade_id=note.trade_id,
        portfolio_id=note.portfolio_id,
        symbol=symbol,
        symbol_candidates=candidates or None,
        market_symbols=await held_symbols(session, note),
        text=note.text,
        summary=note.summary,
        proposal=note.proposal,
        changes=[change_view(c) for c in changes],
        audio_url=audio_url,
        duration_ms=note.duration_ms,
        transcription_provider=note.transcription_provider,
        transcription_model=note.transcription_model,
        extraction_provider=note.extraction_provider,
        extraction_model=note.extraction_model,
        failed_stage=note.failed_stage,
        error_message=note.error_message,
        approved_at=note.approved_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


async def trade_summary(
    session: AsyncSession, trade: Trade, capital: Decimal | None = None
) -> TradeSummaryView:
    """A trade's numbers, all derived from its changes at read time.

    ``capital`` is the owner's account capital, passed in rather than looked up
    so a list of trades costs one query, not one per trade.
    """
    changes = await changes_for_trade(session, trade.id)
    closed = trade.closed_at is not None
    note_count = (
        await session.scalar(
            select(func.count()).select_from(Note).where(Note.trade_id == trade.id)
        )
        or 0
    )
    last_note_at = await session.scalar(
        select(Note.created_at)
        .where(Note.trade_id == trade.id)
        .order_by(Note.created_at.desc())
        .limit(1)
    )
    holding_days = None
    if trade.closed_at is not None:
        holding_days = (trade.closed_at - trade.opened_at).total_seconds() / 86400
    return TradeSummaryView(
        id=trade.id,
        number=trade.number,
        symbol=trade.symbol,
        currency=trade.currency,
        portfolio_id=trade.portfolio_id,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        is_open=not closed,
        shares=d(pos.shares_held(changes)) or "0",
        average_price=d(pos.q8(pos.average_price(changes))),
        current_stop=d(pos.current_stop(changes)),
        initial_stop=d(pos.initial_stop(changes)),
        current_target=d(pos.current_target(changes)),
        planned_r=d(pos.q4(pos.planned_r(changes))),
        realized_pnl=d(pos.q8(pos.realized_pnl(changes))),
        r_multiple=d(pos.q4(pos.r_multiple(changes))),
        holding_days=round(holding_days, 2) if holding_days is not None else None,
        note_count=note_count,
        last_note_at=last_note_at,
        position_size_percent=d(pos.position_size_percent(changes, capital)),
    )


# --------------------------------------------------------------------------
# the applier
# --------------------------------------------------------------------------


def _missing(field: str, context: str) -> IncompleteNoteError:
    return IncompleteNoteError(f"{context} Add {field} in the review screen and approve again.")


def _validate(kind: str, c: ChangeProposal, position: Decimal) -> None:
    """Every value the change needs must be present. Missing values name
    themselves so the trader can fill them in the review screen."""
    if kind == "OPEN":
        if c.shares is None:
            raise _missing(
                "the share count",
                "This note opens a position but does not say how many shares.",
            )
        if c.price is None:
            raise _missing(
                "the entry price",
                "This note opens a position but does not say at what price.",
            )
    elif kind == "ADD":
        if c.shares is None:
            raise _missing(
                "the share count", "This note buys more but does not say how many shares."
            )
        if c.price is None:
            raise _missing(
                "the price", "This note buys more but does not say at what price."
            )
    elif kind == "TRIM":
        if c.shares is None and c.fraction is None:
            raise _missing(
                "the share count or a fraction",
                "This note sells part of the position but does not say how much.",
            )
        if c.shares is None and position <= 0:
            raise IncompleteNoteError(
                "This note sells a part of the position, but there is no open position to take a part of."
            )
    elif kind == "STOP":
        if c.stop is None:
            raise _missing(
                "the new stop price", "This note moves the stop but does not give the new price."
            )
    elif kind == "TARGET":
        if c.target is None:
            raise _missing(
                "the new target price",
                "This note moves the target but does not give the new price.",
            )


def _shares_from_fraction(position: Decimal, fraction: Decimal) -> Decimal:
    return position * fraction


async def _resolve_trade(
    session: AsyncSession,
    user: User,
    note: Note,
    symbols: list[str],
    needs_trade: bool,
) -> Trade | None:
    """Find the trade a follow-up note belongs to, and refuse when it cannot.

    The resolution itself lives in ``resolve_note_trade``, which the review
    screen calls too, so what the screen shows and what this applies can never
    disagree. This wrapper adds the applier's refusals: ambiguity is reported
    with the open trades named, never guessed.
    """
    resolution = await resolve_note_trade(
        session, user.id, note, symbols, needs_trade, lock=True
    )
    if resolution.trade is not None:
        return resolution.trade
    if resolution.trade_id_missing:
        raise TradeNotFoundError("Trade not found")
    if needs_trade and resolution.candidates:
        # Only a note that CHANGES something is refused for ambiguity: it has to
        # land somewhere, so the trader must say where. An observation applies
        # nothing — its candidates are an offer to file it, never a demand.
        open_symbols = ", ".join(t.symbol for t in resolution.candidates)
        raise AmbiguousTradeError(
            f"This note does not say which trade it belongs to. Your open trades: {open_symbols}. "
            "Record the note from the trade page, or name the symbol."
        )
    if needs_trade:
        raise AmbiguousTradeError(
            "This note changes a position, but you have no open trade. Open the trade first, or record the note from its page."
        )
    return None


async def apply_note(
    session: AsyncSession,
    user: User,
    note: Note,
    now: datetime | None = None,
    entitlements: Entitlements | None = None,
    summarizer: summary_service.Summarizer | None = None,
) -> ApproveResultView:
    """Approve a note: validate, resolve the trade, write the change rows."""
    now = now or datetime.now(timezone.utc)

    if note.status == STATUS_APPROVED:
        return await _replay(session, note)
    if note.status != STATUS_EXTRACTED:
        raise WrongNoteStateError(
            f"This note is {note.status}. Only an extracted note can be approved."
        )

    proposal = NoteProposal.model_validate(note.proposal or {})
    changes = proposal.changes
    # The tickers this note's words point at, verified against the market. The
    # model's own reading is never taken on its word: a candidate that does not
    # trade here is dropped, and its name is looked up instead.
    read = await read_symbols(session, note)
    symbol = read[0] if read else None
    log.info(
        "approve note %s: market=%s symbols=%s changes=%d",
        note.id,
        note.portfolio_id,
        read or "none resolved",
        len(changes),
    )

    opens = [c for c in changes if c.kind == "OPEN"]
    if len(opens) > 1:
        raise IncompleteNoteError("One note can open only one trade.")
    if opens and len(changes) > 1:
        raise IncompleteNoteError(
            "This note opens a new trade and also changes another one. Split it into two notes."
        )
    if opens and note.trade_id is not None:
        raise AmbiguousTradeError(
            "This note was recorded for an existing trade, but it opens a new one."
        )

    created = False
    if opens:
        open_change = opens[0]
        if symbol is None:
            raise _missing(
                "the symbol", "This note opens a position but does not name the instrument."
            )
        _validate("OPEN", open_change, Decimal("0"))
        existing = await session.scalar(
            select(Trade)
            .where(
                Trade.user_id == user.id,
                Trade.symbol == symbol,
                Trade.closed_at.is_(None),
            )
            .with_for_update()
        )
        if existing is not None:
            raise PositionViolationError(
                f"You already have an open {symbol} trade. Record a follow-up note on it instead."
            )
        # The plan's active-trade limit. Checked here, at the only place a trade
        # is created, and never for a change to a trade already held.
        policy = entitlements or Entitlements.from_settings(settings)
        await policy.check_can_open_trade(session, user)
        trade = Trade(
            user_id=user.id,
            symbol=symbol,
            number=await next_trade_number(session, user.id),
            # The market the note was recorded into. This is what keeps markets
            # from mixing: the trade lands in the same portfolio as its note.
            portfolio_id=note.portfolio_id,
            opened_at=open_change.occurred_at or note.created_at,
        )
        session.add(trade)
        await session.flush()
        created = True
    else:
        trade = await _resolve_trade(session, user, note, read, needs_trade=bool(changes))

    if trade is None:
        # A pure observation: approved, nothing to apply. There is no action to
        # summarise, so the line says what the trader saw, from their own words.
        note.summary = await summary_service.write_summary(
            [],
            language=summary_service.language_of(proposal.language, note.text),
            summarizer=summarizer,
            text=note.text,
        )
        note.status = STATUS_APPROVED
        note.approved_at = now
        await session.flush()
        return ApproveResultView(
            note_id=note.id, trade_id=None, created_trade=False, changes_applied=0
        )

    if trade.closed_at is not None and changes:
        raise TradeClosedError(
            f"The {trade.symbol} trade is closed, so it takes no more position changes. "
            "Open a new trade, or record a note with no change."
        )

    existing_changes = await changes_for_trade(session, trade.id)
    position = pos.shares_held(existing_changes)

    rows: list[Change] = []
    for c in changes:
        _validate(c.kind, c, position)
        occurred_at = c.occurred_at or note.created_at
        shares = c.shares

        if c.kind == "OPEN":
            position = shares or Decimal("0")
        elif c.kind == "ADD":
            position += shares or Decimal("0")
        elif c.kind == "TRIM":
            if shares is None and c.fraction is not None:
                shares = _shares_from_fraction(position, c.fraction)
            if shares is None:  # pragma: no cover - _validate rejects this
                raise IncompleteNoteError("The sale has no share count.")
            if shares > position:
                raise PositionViolationError(
                    f"Selling {shares} shares exceeds the {position} shares held in {trade.symbol}."
                )
            position -= shares
        elif c.kind == "CLOSE":
            shares = position
            position = Decimal("0")
            trade.closed_at = occurred_at
        elif c.kind == "STOP":
            pass
        elif c.kind == "TARGET":
            pass

        rows.append(
            Change(
                note_id=note.id,
                kind=c.kind,
                occurred_at=occurred_at,
                shares=shares,
                price=c.price,
                stop=c.stop,
                target=c.target,
            )
        )

    for row in rows:
        session.add(row)

    # The summary is written from the rows that are being applied, not from the
    # draft the model read. The trader's edits are what count, so a line can
    # never describe a proposal that was changed at review.
    note.summary = await summary_service.write_summary(
        rows,
        symbol=trade.symbol,
        language=summary_service.language_of(proposal.language, note.text),
        summarizer=summarizer,
        text=note.text,
    )

    note.trade_id = trade.id
    note.status = STATUS_APPROVED
    note.approved_at = now
    await session.flush()

    # A trade has two dimensions, and they are recorded at different moments. A
    # prediction is made when a trade closes; a resolution is read from a later
    # note on that same, already-closed, trade. Neither touches the other, and
    # neither touches the trade result.
    if proposal.prediction is not None:
        await _record_prediction(session, user, note, trade, proposal.prediction)
    if proposal.resolution is not None:
        await _resolve_prediction(session, trade, note, proposal.resolution)

    applied = await changes_for_trade(session, trade.id)
    return ApproveResultView(
        note_id=note.id,
        trade_id=trade.id,
        created_trade=created,
        changes_applied=len(rows),
        warnings=pos.warnings(applied, closed=trade.closed_at is not None),
    )


async def _replay(session: AsyncSession, note: Note) -> ApproveResultView:
    """Idempotent second approval: report the stored result, change nothing."""
    rows = await changes_for_note(session, note.id)
    applied = (
        await changes_for_trade(session, note.trade_id) if note.trade_id else []
    )
    return ApproveResultView(
        note_id=note.id,
        trade_id=note.trade_id,
        created_trade=any(r.kind == "OPEN" for r in rows),
        changes_applied=len(rows),
        warnings=pos.warnings(applied, closed=False),
    )
