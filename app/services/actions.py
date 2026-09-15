"""The atomic action processor — the single engine behind BOTH entry points:
the voice pipeline (approve) and manual journaling (apply_manual). One
canonical strategy, two front doors.

Every approved journal entry is one domain transaction. The journal entry row
is locked FOR UPDATE at the start, so:
  - idempotency: status == APPROVED is the commit marker; a retried approval
    replays the stored result without re-applying;
  - concurrency: two simultaneous approvals of the same note serialize on the
    row lock, and the second one replays.
Actions are applied strictly in payload order against an in-memory running
state (position, decisions, outcome), all-or-nothing on commit.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import management as mgmt
from app.domain import pnl, position
from app.domain.errors import (
    DomainError,
    JournalEntryNotFoundError,
    MultiTradeNoteError,
    OutcomeExistsError,
    StaleValueError,
    TradeCompletedError,
    TradeNotFoundError,
    WrongJournalStateError,
)
from app.models import (
    Execution,
    JournalEntry,
    ManagementDecision,
    Trade,
    TradeOutcome,
    User,
)
from app.schemas import api as views
from app.schemas.actions import (
    CURRENT_ACTIVE_TRADE,
    ActionsPayload,
    AddExecutionAction,
    AddOutcomeAction,
    ChangeStopAction,
    ChangeTargetAction,
    CompleteTradeAction,
    CreateTradeAction,
)
from app.schemas.api import ApprovalResultView

STATUS_APPROVED = "APPROVED"
STATUS_EXTRACTED = "EXTRACTED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _ApplyState:
    """Running state for one Trade while a payload is applied."""

    def __init__(self, trade: Trade):
        self.trade = trade
        self.executions: list[Execution] = []
        self.decisions: list[ManagementDecision] = []
        self.outcome: TradeOutcome | None = None
        self.position: Decimal = Decimal("0")
        self.warnings: list[str] = []


async def _load_trade_state(
    session: AsyncSession, state: _ApplyState
) -> None:
    """Hydrate the running state from the DB (call once per trade)."""
    state.executions = list(
        await session.scalars(
            select(Execution)
            .where(Execution.trade_id == state.trade.id)
            .order_by(Execution.executed_at, Execution.created_at)
        )
    )
    state.position = position.net_position(state.executions)
    state.decisions = list(
        await session.scalars(
            select(ManagementDecision)
            .where(ManagementDecision.trade_id == state.trade.id)
            .order_by(ManagementDecision.decided_at, ManagementDecision.created_at)
        )
    )
    state.outcome = await session.scalar(
        select(TradeOutcome).where(TradeOutcome.trade_id == state.trade.id)
    )


def _require_active(state: _ApplyState, noun: str) -> None:
    if state.trade.status == "COMPLETED":
        raise TradeCompletedError(
            f"{noun.capitalize()} rejected: Trade {state.trade.id} is COMPLETED; "
            "reopening/correction is a future explicit workflow"
        )


def _referenced_trade_ids(payload: ActionsPayload) -> set[UUID | str]:
    """Raw refs from a payload: real UUIDs or the CURRENT_ACTIVE_TRADE sentinel."""
    refs: set[UUID | str] = set()
    for a in payload.actions:
        if isinstance(
            a,
            (
                AddExecutionAction,
                ChangeStopAction,
                ChangeTargetAction,
                AddOutcomeAction,
                CompleteTradeAction,
            ),
        ):
            refs.add(a.trade_id)
    return refs


async def _resolve_trade_refs(
    session: AsyncSession, user: User, refs: set[UUID | str]
) -> tuple[dict[UUID | str, UUID], set[UUID]]:
    """Resolve every ref to a concrete owned Trade id.

    CURRENT_ACTIVE_TRADE (the extractor's "same trade" sentinel — it never
    invents ids) resolves to the user's most recent ACTIVE Trade. If none is
    open there is nothing safe to mutate, so the note is rejected (spec:
    "Resolving later notes" — the backend resolves/verifies, the client never
    supplies a trusted id).
    """
    resolved: dict[UUID | str, UUID] = {}
    concrete: set[UUID] = set()
    for ref in refs:
        if isinstance(ref, str):  # the sentinel
            trade = await session.scalar(
                select(Trade)
                .where(Trade.user_id == user.id, Trade.status == "ACTIVE")
                .order_by(Trade.opened_at.desc())
                .limit(1)
            )
            if trade is None:
                raise DomainError(
                    "This note refers to your active Trade, but you have no open "
                    "Trade — open one first or reference the Trade explicitly"
                )
            resolved[ref] = trade.id
            concrete.add(trade.id)
        else:
            concrete.add(ref)
    return resolved, concrete


def _require_complete(*args) -> None:
    """Create a friendly 422 naming the missing field. The proposal may carry
    nulls (the AI never invents facts); a Trade cannot be created until the
    trader fills them at review."""
    for value, label in args:
        if value is None:
            raise DomainError(
                f"{label} is required to complete this trade — fill it in at "
                "review and approve again"
            )


async def apply_approved_actions(
    session: AsyncSession,
    user: User,
    payload: ActionsPayload,
    entry: JournalEntry | None = None,
) -> ApprovalResultView:
    # --- scope rules ---
    create_actions = [a for a in payload.actions if isinstance(a, CreateTradeAction)]
    if create_actions and len(payload.actions) > 1:
        raise MultiTradeNoteError(
            "CREATE_TRADE embeds its initial execution and must be the only "
            "action in a note"
        )
    trade_ids = _referenced_trade_ids(payload)
    if len(trade_ids) > 1:
        raise MultiTradeNoteError("a note may target only one Trade")

    # The extractor emits CURRENT_ACTIVE_TRADE instead of inventing an id;
    # resolve it to the user's real Trade before anything touches UUIDs.
    resolved_refs, concrete_ids = await _resolve_trade_refs(session, user, trade_ids)

    def _concrete(ref: UUID | str) -> UUID:
        if isinstance(ref, str):
            resolved = resolved_refs.get(ref)
            if resolved is None:  # pragma: no cover - every str ref is resolved
                raise AssertionError(f"unresolved trade ref {ref}")
            return resolved
        return ref

    # --- load referenced trades with ownership as the boundary ---
    trades: dict[UUID, Trade] = {}
    if concrete_ids:
        rows = await session.scalars(
            select(Trade).where(Trade.id.in_(concrete_ids), Trade.user_id == user.id)
        )
        trades = {t.id: t for t in rows}
        if len(trades) != len(concrete_ids):
            # Identical response for missing and foreign ids: no existence leak.
            raise TradeNotFoundError("Trade not found")

    state_by_trade: dict[UUID, _ApplyState] = {}

    def state_for(trade: Trade) -> _ApplyState:
        state = state_by_trade.get(trade.id)
        if state is None:
            state = _ApplyState(trade)
            state_by_trade[trade.id] = state
        return state

    created_trade: Trade | None = None
    for action in payload.actions:
        if isinstance(action, CreateTradeAction):
            created_trade = Trade(
                user_id=user.id,
                symbol=action.symbol,
                direction=action.direction,
                pattern=action.pattern,
                status="ACTIVE",
                opened_at=action.opened_at,
                rationale=action.rationale,
                failure_point=action.failure_point,
                target_price=action.target_price,
            )
            session.add(created_trade)
            await session.flush()  # id needed by executions / entry link
            state_by_trade[created_trade.id] = _ApplyState(created_trade)
            break

    # --- apply in payload order, all against the running state ---
    for action in payload.actions:
        if isinstance(action, CreateTradeAction):
            created = created_trade
            assert created is not None  # set in the pre-pass above
            _require_complete(
                (action.symbol, "symbol"),
                (action.direction, "direction"),
                (action.pattern, "pattern (choose one of the supported chart patterns)"),
            )
            state = state_by_trade[created.id]
            for spec in action.executions:
                _require_complete(
                    (spec.side, "execution side"),
                    (spec.quantity, "execution quantity"),
                    (spec.price, "execution price"),
                    (spec.executed_at, "execution time"),
                )
                state.position = position.apply_execution(
                    state.position, created.direction, spec.side, spec.quantity
                )
                session.add(
                    Execution(
                        trade_id=created.id,
                        side=spec.side,
                        quantity=spec.quantity,
                        price=spec.price,
                        executed_at=spec.executed_at,
                        reason=spec.reason,
                    )
                )

        elif isinstance(action, AddExecutionAction):
            state = state_for(trades[_concrete(action.trade_id)])
            if not state.executions:
                await _load_trade_state(session, state)
            _require_active(state, "execution")
            _require_complete(
                (action.side, "execution side"),
                (action.quantity, "execution quantity"),
                (action.price, "execution price"),
                (action.executed_at, "execution time"),
            )
            state.position = position.apply_execution(
                state.position, state.trade.direction, action.side, action.quantity
            )
            session.add(
                Execution(
                    trade_id=state.trade.id,
                    side=action.side,
                    quantity=action.quantity,
                    price=action.price,
                    executed_at=action.executed_at,
                    reason=action.reason,
                )
            )

        elif isinstance(action, (ChangeStopAction, ChangeTargetAction)):
            state = state_for(trades[_concrete(action.trade_id)])
            if not state.decisions:
                await _load_trade_state(session, state)
            _require_active(state, "management change")
            if isinstance(action, ChangeStopAction):
                decision_type = mgmt.STOP_LOSS_CHANGE
                current = mgmt.current_stop(state.trade, state.decisions)
                noun = "stop"
            else:
                decision_type = mgmt.TARGET_CHANGE
                current = mgmt.current_target(state.trade, state.decisions)
                noun = "target"
            if action.old_value is not None and (
                current is None or action.old_value != current
            ):
                raise StaleValueError(
                    f"The {noun} is currently {current if current is not None else 'unset'}, "
                    f"not {action.old_value}; this note was built from stale state and "
                    "was not applied (nothing was overwritten)"
                )
            decision = ManagementDecision(
                trade_id=state.trade.id,
                type=decision_type,
                old_value=action.old_value if action.old_value is not None else current,
                new_value=action.new_value,
                reason=action.reason,
                decided_at=_now(),
            )
            session.add(decision)
            state.decisions.append(decision)

        elif isinstance(action, AddOutcomeAction):
            state = state_for(trades[_concrete(action.trade_id)])
            if state.outcome is None:
                await _load_trade_state(session, state)
            if state.outcome is not None:
                raise OutcomeExistsError(
                    f"Trade {state.trade.id} already has an outcome; "
                    "one outcome per Trade"
                )
            state.outcome = TradeOutcome(
                trade_id=state.trade.id,
                type=action.type,
                result=action.result,
                lesson=action.lesson,
                notes=action.notes,
                completed_at=_now(),  # "when the outcome was recorded"
            )
            session.add(state.outcome)

        elif isinstance(action, CompleteTradeAction):
            state = state_for(trades[_concrete(action.trade_id)])
            if not state.executions:
                await _load_trade_state(session, state)
            if state.trade.status == "COMPLETED":
                raise TradeCompletedError(
                    f"Trade {state.trade.id} is already COMPLETED"
                )
            state.trade.status = "COMPLETED"
            state.trade.completed_at = action.completed_at
            if state.position != 0:
                state.warnings.append(
                    f"Trader declared COMPLETED but the derived net position is "
                    f"{state.position}; the trader's declaration is authoritative "
                    "and was kept (see Completion Semantics)"
                )

    # --- finalize the journal entry (voice path only) ---
    if entry is not None:
        entry.trade_id = (
            created_trade.id
            if created_trade is not None
            else (_concrete(next(iter(trade_ids))) if trade_ids else None)
        )
        entry.status = STATUS_APPROVED
        entry.approved_at = _now()
        entry.approved_payload = payload.model_dump(mode="json")

    all_trade_ids = set(trades.keys())
    if created_trade is not None:
        all_trade_ids.add(created_trade.id)
    warning = next(
        (w for st in state_by_trade.values() for w in st.warnings), None
    )
    return ApprovalResultView(
        journal_entry_id=entry.id if entry is not None else None,
        trade_ids=sorted(all_trade_ids),
        created_trade_id=created_trade.id if created_trade is not None else None,
        warning=warning,
    )


async def _replay(
    session: AsyncSession, user: User, entry: JournalEntry
) -> ApprovalResultView:
    """Idempotent replay: rebuild the result from the stored approved payload.

    The stored payload may carry the CURRENT_ACTIVE_TRADE sentinel (that is
    what the user approved); resolve it the same way the applier does.
    """
    stored = entry.approved_payload or {"actions": []}
    actions = stored.get("actions", [])
    created = (
        entry.trade_id
        if any(a.get("action") == "CREATE_TRADE" for a in actions)
        else None
    )
    trade_ids: set[UUID] = set()
    for a in actions:
        if a.get("action") == "CREATE_TRADE" or not a.get("trade_id"):
            continue
        ref = a["trade_id"]
        if isinstance(ref, str) and ref == CURRENT_ACTIVE_TRADE:
            trade = await session.scalar(
                select(Trade)
                .where(Trade.user_id == user.id, Trade.status == "ACTIVE")
                .order_by(Trade.opened_at.desc())
                .limit(1)
            )
            if trade is not None:
                trade_ids.add(trade.id)
        else:
            try:
                trade_ids.add(UUID(str(ref)))
            except ValueError:
                continue
    if created is not None:
        trade_ids.add(created)
    return ApprovalResultView(
        journal_entry_id=entry.id,
        trade_ids=sorted(trade_ids),
        created_trade_id=created,
    )


async def approve_journal_entry(
    session: AsyncSession,
    user: User,
    entry_id: UUID,
    payload: ActionsPayload,
) -> ApprovalResultView:
    """Approve one journal entry: lock, validate, apply atomically, commit.

    Repeated calls with any payload after success return the stored result
    without touching the domain again (the timeout/retry case).
    """
    entry = await session.scalar(
        select(JournalEntry)
        .where(JournalEntry.id == entry_id, JournalEntry.user_id == user.id)
        .with_for_update()
    )
    if entry is None:
        raise JournalEntryNotFoundError("Journal entry not found")

    if entry.status == STATUS_APPROVED:
        return await _replay(session, user, entry)

    if entry.status != STATUS_EXTRACTED:
        raise WrongJournalStateError(
            f"Journal entry is {entry.status}; only EXTRACTED notes can be approved"
        )

    result = await apply_approved_actions(session, user, payload, entry)
    await session.commit()
    return result


async def apply_manual(
    session: AsyncSession, user: User, payload: ActionsPayload
) -> ApprovalResultView:
    """Manual journaling path: the same engine, no journal entry, no allowance.

    The form submit IS the approval, so actions are applied immediately.
    """
    result = await apply_approved_actions(session, user, payload)
    await session.commit()
    return result