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
from app.models import Change, Note, Trade, User
from app.schemas.api import ApproveResultView, ChangeView, NoteView, TradeSummaryView, d
from app.schemas.proposal import ChangeProposal, NoteProposal

STATUS_RECEIVED = "RECEIVED"
STATUS_TRANSCRIBED = "TRANSCRIBED"
STATUS_EXTRACTED = "EXTRACTED"
STATUS_APPROVED = "APPROVED"
STATUS_FAILED = "FAILED"


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


async def note_symbol(session: AsyncSession, note: Note) -> str | None:
    """The symbol a note belongs to.

    Order, and each step is a fact rather than a guess:

    1. The symbol of the note's TRADE — the one it was recorded against, or the
       one its changes were applied to (the applier stamps ``note.trade_id``).
    2. The symbol the model read in the trader's words.
    3. The only open trade, when the note changes a position but has no trade
       context yet: it was recorded from the journal, so the trader never named
       the trade, and there is exactly one candidate. This mirrors the applier's
       own resolution (``_resolve_trade`` is the authority); with two or more
       open trades the answer is genuinely unknown and stays None.

    A note that OPENS a trade never falls to step 3: an opening note is a new
    trade and cannot inherit the symbol of one already held.

    Step 1 and 3 exist because a follow-up note never repeats the symbol — the
    trader is already inside that trade — so the review screen would otherwise
    show an empty Symbol field on every follow-up.
    """
    if note.trade_id is not None:
        symbol = await session.scalar(
            select(Trade.symbol).where(Trade.id == note.trade_id)
        )
        if symbol:
            return symbol

    proposal = note.proposal if isinstance(note.proposal, dict) else None
    read = (proposal or {}).get("symbol")
    if isinstance(read, str) and read.strip():
        return read.strip()

    changes = (proposal or {}).get("changes") or []
    if not changes or any(c.get("kind") == "OPEN" for c in changes):
        return None
    open_symbols = list(
        await session.scalars(
            select(Trade.symbol)
            .where(Trade.user_id == note.user_id, Trade.closed_at.is_(None))
            .order_by(Trade.opened_at.desc())
            .limit(2)
        )
    )
    return open_symbols[0] if len(open_symbols) == 1 else None


async def note_view(session: AsyncSession, note: Note, storage=None) -> NoteView:
    changes = await changes_for_note(session, note.id)
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
        symbol=await note_symbol(session, note),
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


async def trade_summary(session: AsyncSession, trade: Trade) -> TradeSummaryView:
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
        symbol=trade.symbol,
        currency=trade.currency,
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
    session: AsyncSession, user: User, note: Note, symbol: str | None, needs_trade: bool
) -> Trade | None:
    """Find the trade a follow-up note belongs to.

    Order: the trade the note was recorded against, then an open trade in the
    named symbol, then the only open trade. Ambiguity is reported, never
    guessed. A note with no change needs no trade at all.
    """
    if note.trade_id is not None:
        trade = await session.scalar(
            select(Trade)
            .where(Trade.id == note.trade_id, Trade.user_id == user.id)
            .with_for_update()
        )
        if trade is None:
            raise TradeNotFoundError("Trade not found")
        return trade

    if symbol:
        trade = await session.scalar(
            select(Trade)
            .where(
                Trade.user_id == user.id,
                Trade.symbol == symbol,
                Trade.closed_at.is_(None),
            )
            .with_for_update()
        )
        if trade is not None:
            return trade

    open_trades = list(
        await session.scalars(
            select(Trade)
            .where(Trade.user_id == user.id, Trade.closed_at.is_(None))
            .order_by(Trade.opened_at.desc())
            .with_for_update()
        )
    )
    if len(open_trades) == 1:
        return open_trades[0]
    if not needs_trade:
        return None
    if not open_trades:
        raise AmbiguousTradeError(
            "This note changes a position, but you have no open trade. Open the trade first, or record the note from its page."
        )
    symbols = ", ".join(t.symbol for t in open_trades)
    raise AmbiguousTradeError(
        f"This note does not say which trade it belongs to. Your open trades: {symbols}. "
        "Record the note from the trade page, or name the symbol."
    )


async def apply_note(
    session: AsyncSession,
    user: User,
    note: Note,
    now: datetime | None = None,
    entitlements: Entitlements | None = None,
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
    symbol = (proposal.symbol or "").strip().upper() or None

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
            opened_at=open_change.occurred_at or note.created_at,
        )
        session.add(trade)
        await session.flush()
        created = True
    else:
        trade = await _resolve_trade(session, user, note, symbol, needs_trade=bool(changes))

    if trade is None:
        # A pure observation: approved, nothing to apply.
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

    note.trade_id = trade.id
    note.status = STATUS_APPROVED
    note.approved_at = now
    await session.flush()

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
