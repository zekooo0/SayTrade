"""Analytics: canonical metric definitions, derived once, from the domain
tables. The same functions back any future dashboard or "Ask your journal" —
never a second implementation of a metric."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import pnl
from app.models import Execution, Trade, TradeOutcome, User
from app.schemas.api import AnalyticsView


def _fmt(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _win_rate(wins: int, losses: int) -> float | None:
    decided = wins + losses
    return wins / decided if decided else None


def _bucket(t: Trade) -> str:
    return t.opened_at.astimezone(timezone.utc).strftime("%Y-%m")


async def summary(session: AsyncSession, user: User) -> AnalyticsView:
    trades = list(
        await session.scalars(
            select(Trade).where(Trade.user_id == user.id).order_by(Trade.opened_at)
        )
    )
    trade_ids = [t.id for t in trades]
    executions: dict = {}
    if trade_ids:
        rows = await session.scalars(
            select(Execution).where(Execution.trade_id.in_(trade_ids))
        )
        for e in rows:
            executions.setdefault(e.trade_id, []).append(e)
    outcomes: dict = {}
    if trade_ids:
        rows = await session.scalars(
            select(TradeOutcome).where(TradeOutcome.trade_id.in_(trade_ids))
        )
        for o in rows:
            outcomes[o.trade_id] = o

    wins = losses = breaks = 0
    pnls: list[Decimal] = []
    rs: list[Decimal] = []
    holding_days: list[float] = []
    disagreements = 0
    by_pattern: dict[str, dict] = {}
    by_type: dict[str, dict] = {}
    over_time: dict[str, dict] = {}

    for t in trades:
        exs = executions.get(t.id, [])
        outcome = outcomes.get(t.id)

        bucket = _bucket(t)
        bt = over_time.setdefault(bucket, {"period": bucket, "count": 0, "pnl": _fmt(Decimal("0"))})
        bt["count"] += 1

        pat = by_pattern.setdefault(
            t.pattern,
            {"pattern": t.pattern, "count": 0, "wins": 0, "losses": 0, "win_rate": None, "pnl": _fmt(Decimal("0"))},
        )
        pat["count"] += 1

        realized = pnl.realized_pnl(exs)  # only fully closed trades
        r = pnl.r_multiple(exs, t.failure_point)

        if outcome is not None:
            if outcome.result == "WIN":
                wins += 1
                pat["wins"] += 1
            elif outcome.result == "LOSS":
                losses += 1
                pat["losses"] += 1
            else:
                breaks += 1
            key = f"Type {outcome.type}" if outcome.type else "Type unknown"
            typ = by_type.setdefault(
                key, {"type": key, "count": 0, "wins": 0, "losses": 0, "win_rate": None, "pnl": _fmt(Decimal("0"))}
            )
            typ["count"] += 1
            if outcome.result == "WIN":
                typ["wins"] += 1
            elif outcome.result == "LOSS":
                typ["losses"] += 1

        if realized is not None:
            pnls.append(realized)
            bt["pnl"] = _fmt(Decimal(bt["pnl"] or "0") + realized)
            pat["pnl"] = _fmt(Decimal(pat["pnl"] or "0") + realized)
            if outcome is not None:
                typ = by_type.setdefault(
                    f"Type {outcome.type}" if outcome.type else "Type unknown", {}
                )
                if typ:
                    typ["pnl"] = _fmt(Decimal(typ.get("pnl") or "0") + realized)
                if (outcome.result == "WIN" and realized < 0) or (
                    outcome.result == "LOSS" and realized > 0
                ) or (outcome.result == "BREAKEVEN" and realized != 0):
                    disagreements += 1
        if r is not None:
            rs.append(r)

        if t.completed_at is not None:
            holding_days.append((t.completed_at - t.opened_at).total_seconds() / 86400.0)

    gross = sum(pnls, Decimal("0")) if pnls else None
    avg_pnl = (gross / len(pnls)) if pnls and gross is not None else None
    total_r = sum(rs, Decimal("0")) if rs else None
    avg_r = (total_r / len(rs)) if rs and total_r is not None else None

    for stat in [*by_pattern.values(), *by_type.values()]:
        if stat:
            stat["win_rate"] = _win_rate(stat["wins"], stat["losses"])
    for stat in over_time.values():
        stat["win_rate"] = _win_rate(
            sum(1 for t in trades if _bucket(t) == stat["period"] and outcomes.get(t.id) and outcomes[t.id].result == "WIN"),
            sum(1 for t in trades if _bucket(t) == stat["period"] and outcomes.get(t.id) and outcomes[t.id].result == "LOSS"),
        )

    return AnalyticsView(
        trade_count=len(trades),
        wins=wins,
        losses=losses,
        breakevens=breaks,
        win_rate=_win_rate(wins, losses),
        gross_pnl=_fmt(gross),
        average_pnl=_fmt(avg_pnl),
        total_r=_fmt(total_r),
        average_r=_fmt(avg_r),
        average_holding_days=(sum(holding_days) / len(holding_days)) if holding_days else None,
        pnl_result_disagreements=disagreements,
        by_pattern=by_pattern,
        by_type=by_type,
        over_time=sorted(over_time.values(), key=lambda b: b["period"]),
    )