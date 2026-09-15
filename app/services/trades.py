"""Trade queries: list, detail (with derived metrics), and the manual path."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import management as mgmt
from app.domain import pnl, position
from app.domain.errors import TradeNotFoundError
from app.models import (
    Execution,
    JournalEntry,
    ManagementDecision,
    Trade,
    TradeOutcome,
    User,
)
from app.schemas import api as views
from app.schemas.actions import ActionsPayload
from app.schemas.api import ApprovalResultView
from app.services.actions import apply_manual


async def list_trades(
    session: AsyncSession, user: User, status: str | None = None
) -> list[views.TradeSummaryView]:
    query = (
        select(Trade)
        .where(Trade.user_id == user.id)
        .order_by(Trade.opened_at.desc())
    )
    if status is not None:
        query = query.where(Trade.status == status)
    trades = list(await session.scalars(query))

    trade_ids = [t.id for t in trades]
    executions: dict[UUID, list[Execution]] = {tid: [] for tid in trade_ids}
    if trade_ids:
        rows = await session.scalars(
            select(Execution)
            .where(Execution.trade_id.in_(trade_ids))
            .order_by(Execution.executed_at, Execution.created_at)
        )
        for e in rows:
            executions[e.trade_id].append(e)
    decisions: dict[UUID, list[ManagementDecision]] = {tid: [] for tid in trade_ids}
    if trade_ids:
        rows = await session.scalars(
            select(ManagementDecision)
            .where(ManagementDecision.trade_id.in_(trade_ids))
            .order_by(ManagementDecision.decided_at, ManagementDecision.created_at)
        )
        for d in rows:
            decisions[d.trade_id].append(d)

    out: list[views.TradeSummaryView] = []
    for t in trades:
        out.append(
            views.TradeSummaryView(
                id=t.id,
                symbol=t.symbol,
                direction=t.direction,
                pattern=t.pattern,
                status=t.status,
                opened_at=t.opened_at,
                completed_at=t.completed_at,
                net_position=views.ds(position.net_position(executions[t.id])),
                current_stop=views.d(mgmt.current_stop(t, decisions[t.id])),
                current_target=views.d(mgmt.current_target(t, decisions[t.id])),
            )
        )
    return out


async def get_trade(
    session: AsyncSession, user: User, trade_id: UUID
) -> views.TradeDetailView:
    trade = await session.scalar(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id)
    )
    if trade is None:
        raise TradeNotFoundError("Trade not found")

    executions = list(
        await session.scalars(
            select(Execution)
            .where(Execution.trade_id == trade.id)
            .order_by(Execution.executed_at, Execution.created_at)
        )
    )
    decisions = list(
        await session.scalars(
            select(ManagementDecision)
            .where(ManagementDecision.trade_id == trade.id)
            .order_by(ManagementDecision.decided_at, ManagementDecision.created_at)
        )
    )
    outcome = await session.scalar(
        select(TradeOutcome).where(TradeOutcome.trade_id == trade.id)
    )
    journal_ids = list(
        await session.scalars(
            select(JournalEntry.id).where(
                JournalEntry.trade_id == trade.id, JournalEntry.user_id == user.id
            )
        )
    )

    net_pos = position.net_position(executions)
    realized = pnl.realized_pnl(executions)
    r = pnl.r_multiple(executions, trade.failure_point)

    warnings: list[str] = []
    if trade.status == "COMPLETED" and net_pos != 0:
        warnings.append(
            f"Declared COMPLETED but derived net position is {net_pos}; "
            "the trader's declaration is authoritative"
        )
    if realized is not None and outcome is not None:
        if (outcome.result == "WIN" and realized < 0) or (
            outcome.result == "LOSS" and realized > 0
        ):
            warnings.append(
                f"Declared result ({outcome.result}) disagrees with the derived "
                f"gross P&L ({realized}); the declaration is kept"
            )

    return views.TradeDetailView(
        id=trade.id,
        symbol=trade.symbol,
        direction=trade.direction,
        pattern=trade.pattern,
        status=trade.status,
        opened_at=trade.opened_at,
        completed_at=trade.completed_at,
        rationale=trade.rationale,
        failure_point=views.d(trade.failure_point),
        target_price=views.d(trade.target_price),
        net_position=views.ds(net_pos),
        current_stop=views.d(mgmt.current_stop(trade, decisions)),
        current_target=views.d(mgmt.current_target(trade, decisions)),
        executions=[
            views.ExecutionView(
                id=e.id,
                side=e.side,
                quantity=views.ds(e.quantity),
                price=views.ds(e.price),
                executed_at=e.executed_at,
                reason=e.reason,
            )
            for e in executions
        ],
        decisions=[
            views.DecisionView(
                id=d.id,
                type=d.type,
                old_value=views.d(d.old_value),
                new_value=views.d(d.new_value),
                reason=d.reason,
                decided_at=d.decided_at,
            )
            for d in decisions
        ],
        outcome=(
            views.OutcomeView(
                id=outcome.id,
                type=outcome.type,
                result=outcome.result,
                lesson=outcome.lesson,
                notes=outcome.notes,
                completed_at=outcome.completed_at,
            )
            if outcome is not None
            else None
        ),
        journal_entry_ids=journal_ids,
        realized_pnl=views.d(realized),
        r_multiple=views.d(r),
        warnings=warnings,
    )


async def manual_apply(
    session: AsyncSession, user: User, payload: ActionsPayload
) -> ApprovalResultView:
    """Manual journaling: the same atomic engine, no journal entry involved."""
    return await apply_manual(session, user, payload)