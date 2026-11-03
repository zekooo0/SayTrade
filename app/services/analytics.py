"""Analytics: the notebook's scoreboard, computed from the change rows.

Everything here is derived on read. A statistic is a pure function over the
closed trades (their derived P&L and R) plus the note rows, so no total is ever
stored and none can drift out of step with the timeline.

Two honesty rules, both load-bearing:

- A trade enters the R statistics only when it has BOTH an initial stop and a
  priced exit. The rest are counted in ``unrated_trades`` and reported, never
  folded in as zero.
- An unpriced sale is not a zero. Those trades are counted in
  ``unpriced_trades`` and kept out of the money statistics.

And one scope rule: the WHOLE view is one market. When the caller names a
market, every figure — the counts, the money, the R statistics, the prediction
accuracy and the habit — covers that market's trades and that market's notes,
so nothing from another book can appear under the wrong name. A note with no
market of its own belongs to no market: it appears only in the unscoped view.
``money_scope`` says which market covered the figures.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.position import q4
from app.models import Change, Note, Portfolio, Trade, User
from app.schemas.api import (
    AnalyticsView,
    EquityPoint,
    MoneyScopeView,
    MonthBucket,
    RBucket,
    StopBreach,
    WeekBucket,
    d,
)
from app.services import notes as note_service
from app.services import predictions as prediction_service
from app.services import trades as trades_service

ZERO = Decimal("0")
ONE = Decimal("1")
# R below this means price ran through the stop the trader set.
BEYOND_STOP = Decimal("-1")
# Edges of the R distribution: [low, high), except the open-ended first and last.
# Labels stay short: they are drawn along a chart axis.
R_BUCKETS: list[tuple[str, Decimal | None, Decimal | None, bool]] = [
    ("≤ -1R", None, Decimal("-1"), True),
    ("-1 to 0R", Decimal("-1"), ZERO, True),
    ("0 to 1R", ZERO, ONE, False),
    ("1 to 2R", ONE, Decimal("2"), False),
    ("2 to 3R", Decimal("2"), Decimal("3"), False),
    ("3R or more", Decimal("3"), None, False),
]
WEEKS_SHOWN = 12


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _s(value: Decimal) -> str:
    """A decimal that is known to be present, as the API's numeric string."""
    return d(value) or "0"


def _bucket_of(r: Decimal) -> str:
    for label, low, high, _loss in R_BUCKETS:
        if low is not None and r < low:
            continue
        if high is not None and r >= high:
            continue
        return label
    return R_BUCKETS[-1][0]


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return q4(sum(values, ZERO) / len(values))


def _drawdown(curve: list[Decimal]) -> Decimal | None:
    """The deepest peak-to-trough fall of the cumulative curve, as a positive money amount."""
    if not curve:
        return None
    peak = curve[0]
    worst = ZERO
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


async def overview(
    session: AsyncSession, user: User, portfolio_id: UUID | None = None
) -> AnalyticsView:
    """The scoreboard.

    ``portfolio_id`` scopes the WHOLE view to one market: its trades, its notes,
    its money. A trader with two books must never see one book's win rate under
    the other's name. With no market named, every market is added together —
    the caller's choice, and only honest when they share a currency.
    """
    query = select(Trade).where(Trade.user_id == user.id)
    if portfolio_id is not None:
        query = query.where(Trade.portfolio_id == portfolio_id)
    trades = list(await session.scalars(query))
    view = AnalyticsView()
    view.total_trades = len(trades)

    if portfolio_id is not None:
        market = await session.get(Portfolio, portfolio_id)
        # A foreign market id is not an error here: the trades query above is
        # already the trader's own, so the figures simply come out empty.
        if market is not None and market.user_id == user.id:
            view.money_scope = MoneyScopeView(
                portfolio_id=market.id,
                name=market.name,
                currency=market.currency,
            )

    # --- one pass over the trades: close each one down to its derived numbers ---
    rows: list[dict] = []
    capitals = await trades_service.capitals_for(session, user)
    for trade in trades:
        summary = await note_service.trade_summary(
            session, trade, capital=capitals.get(trade.portfolio_id)
        )
        if summary.is_open:
            view.open_trades += 1
            continue
        view.closed_trades += 1
        rows.append(
            {
                "id": trade.id,
                "symbol": trade.symbol,
                "portfolio_id": trade.portfolio_id,
                "closed_at": summary.closed_at,
                "holding_days": summary.holding_days,
                "pnl": Decimal(summary.realized_pnl)
                if summary.realized_pnl is not None
                else None,
                "r": Decimal(summary.r_multiple)
                if summary.r_multiple is not None
                else None,
                # Why a trade can be missing an R: these two flags are what the
                # left-out counts are built from, so the reasons can never
                # disagree with the sample.
                "has_stop": summary.initial_stop is not None,
            }
        )

    # --- every figure below is money, so it uses the priced trades ---
    # The market filter was applied to the query above: this list is one book,
    # and no other market can leak into it.
    priced = [row for row in rows if row["pnl"] is not None]
    pnls = [row["pnl"] for row in priced]
    holding_days = [
        row["holding_days"] for row in rows if row["holding_days"] is not None
    ]
    view.wins = sum(1 for p in pnls if p > ZERO)
    view.losses = sum(1 for p in pnls if p < ZERO)
    view.breakeven = sum(1 for p in pnls if p == ZERO)
    decided = view.wins + view.losses + view.breakeven
    if decided:
        view.win_rate = round(view.wins / decided, 4)

    # --- money ---
    if pnls:
        total = sum(pnls, ZERO)
        view.total_pnl = d(total)
        view.average_pnl = d(total / len(pnls))
        view.best_pnl = d(max(pnls))
        view.worst_pnl = d(min(pnls))
        gross_profit = sum((p for p in pnls if p > ZERO), ZERO)
        gross_loss = sum((p for p in pnls if p < ZERO), ZERO)
        view.gross_profit = d(gross_profit)
        view.gross_loss = d(gross_loss)
        # No loss yet: the ratio is undefined, not infinite. Report nothing.
        if gross_loss < ZERO:
            view.profit_factor = d(q4(gross_profit / abs(gross_loss)))
    if holding_days:
        view.average_holding_days = round(sum(holding_days) / len(holding_days), 2)

    # --- the equity curve: closed trades in close-date order ---
    ordered = sorted(priced, key=lambda row: (row["closed_at"], str(row["id"])))
    running = ZERO
    curve: list[Decimal] = []
    for row in ordered:
        running += row["pnl"]
        curve.append(running)
        view.equity_curve.append(
            EquityPoint(
                trade_id=row["id"],
                symbol=row["symbol"],
                closed_at=row["closed_at"],
                pnl=_s(row["pnl"]),
                cumulative_pnl=_s(running),
                r_multiple=d(row["r"]),
            )
        )
    view.max_drawdown = d(_drawdown(curve))

    # --- R statistics, over the rated trades only ---
    rated = [row for row in rows if row["r"] is not None]
    view.rated_trades = len(rated)
    view.unrated_trades = view.closed_trades - len(rated)
    # The reasons, as a PARTITION of unrated_trades so the three always add up
    # and no trade is counted twice: a priced exit is what makes row 2 and 3
    # possible, so the three groups cannot overlap.
    view.unpriced_trades = sum(1 for row in rows if row["pnl"] is None)
    view.unrated_no_stop = sum(
        1
        for row in rows
        if row["r"] is None and row["pnl"] is not None and not row["has_stop"]
    )
    view.unrated_no_risk = sum(
        1
        for row in rows
        if row["r"] is None and row["pnl"] is not None and row["has_stop"]
    )
    rs = [row["r"] for row in rated]
    wins_r = [r for r in rs if r > ZERO]
    losses_r = [r for r in rs if r < ZERO]
    if rs:
        view.average_r = d(_mean(rs))
        view.best_r = d(q4(max(rs)))
        view.worst_r = d(q4(min(rs)))
        view.win_rate_r = round(len(wins_r) / len(rs), 4)
        # Expectancy: the average R of a trade under this method. Losers are
        # already negative, so this is a plain mean, not a difference.
        view.expectancy_r = d(_mean(rs))
    view.average_win_r = d(_mean(wins_r))
    view.average_loss_r = d(_mean(losses_r))

    # --- stop discipline ---
    breaches = sorted((row for row in rated if row["r"] < BEYOND_STOP), key=lambda r: r["r"])
    view.beyond_stop = len(breaches)
    view.beyond_stop_trades = [
        StopBreach(
            trade_id=row["id"],
            symbol=row["symbol"],
            r_multiple=_s(row["r"]),
            closed_at=row["closed_at"],
        )
        for row in breaches
    ]

    # --- the R distribution ---
    counts = {label: 0 for label, *_ in R_BUCKETS}
    for r in rs:
        counts[_bucket_of(r)] += 1
    view.r_distribution = [
        RBucket(label=label, count=counts[label], loss=loss)
        for label, _low, _high, loss in R_BUCKETS
    ]

    # --- month by month ---
    months: dict[str, dict] = {}
    for row in ordered:
        key = row["closed_at"].strftime("%Y-%m")
        entry = months.setdefault(key, {"pnl": ZERO, "trades": 0, "wins": 0})
        entry["pnl"] += row["pnl"]
        entry["trades"] += 1
        if row["pnl"] > ZERO:
            entry["wins"] += 1
    view.monthly = [
        MonthBucket(month=key, pnl=_s(entry["pnl"]), trades=entry["trades"], wins=entry["wins"])
        for key, entry in sorted(months.items())
    ]

    # --- the journal habit ---
    await _habit(session, user, view, portfolio_id)

    # --- the other dimension ---
    # The trade result above is what the position did. This is what the trader
    # expected it to do. Separate numbers, on purpose.
    view.prediction_accuracy = await prediction_service.accuracy(
        session, user, portfolio_id
    )
    return view


async def _habit(
    session: AsyncSession, user: User, view: AnalyticsView, portfolio_id: UUID | None = None
) -> None:
    """Notes per week, the current streak, and notes that changed nothing.

    Days and weeks use UTC, matching the rest of the API. A streak still counts
    when the most recent note was yesterday, so it survives a morning check-in.

    With a market named, only that market's notes count. A note that carries no
    market belongs to no market, so it appears in the unscoped view alone — the
    alternative is the same note counted under both books, which is the bug this
    scope exists to prevent.
    """
    query = select(Note).where(Note.user_id == user.id).order_by(Note.created_at)
    if portfolio_id is not None:
        query = query.where(Note.portfolio_id == portfolio_id)
    notes = list(await session.scalars(query))
    view.notes_total = len(notes)
    view.notes_voice = sum(1 for n in notes if n.source == "VOICE")
    view.notes_image = sum(1 for n in notes if n.source == "IMAGE")
    # Counted, not subtracted: IMAGE is its own source, and folding it into
    # "typed" would put screenshots under a word that means something else.
    view.notes_typed = sum(1 for n in notes if n.source == "TEXT")

    changed_ids = set(
        await session.scalars(
            select(Change.note_id).where(Change.note_id.in_([n.id for n in notes])).distinct()
        )
    ) if notes else set()
    view.observation_notes = sum(1 for n in notes if n.id not in changed_ids)

    today = datetime.now(timezone.utc).date()
    this_week = _week_start(today)
    windows = {
        this_week - timedelta(weeks=offset): 0 for offset in range(WEEKS_SHOWN - 1, -1, -1)
    }
    for note in notes:
        week = _week_start(note.created_at.date())
        if week in windows:
            windows[week] += 1
    view.notes_per_week = [
        WeekBucket(week_start=week.isoformat(), notes=count)
        for week, count in sorted(windows.items())
    ]

    days = {note.created_at.date() for note in notes}
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    view.note_streak_days = streak
