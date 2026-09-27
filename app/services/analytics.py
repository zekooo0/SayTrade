"""Analytics: the notebook's scoreboard, computed from the change rows."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import position as pos
from app.models import Trade, User
from app.schemas.api import AnalyticsView, d
from app.services import notes as note_service

ZERO = Decimal("0")


async def overview(session: AsyncSession, user: User) -> AnalyticsView:
    trades = list(await session.scalars(select(Trade).where(Trade.user_id == user.id)))
    view = AnalyticsView()
    view.total_trades = len(trades)

    closed_pnls: list[Decimal] = []
    closed_rs: list[Decimal] = []
    holding_days: list[float] = []

    for trade in trades:
        summary = await note_service.trade_summary(session, trade)
        if summary.is_open:
            view.open_trades += 1
            continue
        view.closed_trades += 1
        if summary.holding_days is not None:
            holding_days.append(summary.holding_days)

        pnl = Decimal(summary.realized_pnl) if summary.realized_pnl is not None else None
        if pnl is not None:
            closed_pnls.append(pnl)
            if pnl > ZERO:
                view.wins += 1
            elif pnl < ZERO:
                view.losses += 1
            else:
                view.breakeven += 1
        if summary.r_multiple is not None:
            closed_rs.append(Decimal(summary.r_multiple))

    decided = view.wins + view.losses + view.breakeven
    if decided:
        view.win_rate = round(view.wins / decided, 4)

    if closed_pnls:
        total = sum(closed_pnls, ZERO)
        view.total_pnl = d(total)
        view.average_pnl = d(total / len(closed_pnls))
    if closed_rs:
        view.average_r = d(sum(closed_rs, ZERO) / len(closed_rs))
        view.best_r = d(max(closed_rs))
        view.worst_r = d(min(closed_rs))
    if holding_days:
        view.average_holding_days = round(sum(holding_days) / len(holding_days), 2)

    return view
