"""The weekly review: one week's numbers, and the one sentence a trader writes.

Two halves, and they follow different rules on purpose.

The NUMBERS are derived on read from the change rows, exactly as the scoreboard
derives its own: trades closed inside the week, the net R, the stops that held.
Nothing is stored, so a review can never disagree with the journal it describes —
a note approved late changes the week, which is the truth of what happened.

The SENTENCE is the opposite: it exists nowhere else. It is the one thing the
trader decided to do differently, and the row in `weekly_reviews` is the only
copy of it.

The review is user-wide, not per market: R is already currency-free, and a trader
who runs two books still has one discipline to keep. No money figure appears on
this screen for that reason.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, Trade, User, WeeklyReview
from app.schemas.api import ReviewNoteView, ReviewView, d
from app.services import notes as note_service
from app.services import trades as trades_service

ZERO = Decimal("0")
# R below this means price ran through the stop the trader set.
BEYOND_STOP = Decimal("-1")
# A week of notes is a reading list, not an archive. Newest first.
NOTES_SHOWN = 20


def week_start_of(moment: datetime) -> date:
    """The Monday of `moment`'s week, in UTC.

    UTC because every other day and week in this API is UTC. A trader in Cairo
    reviewing on a Friday evening and one in New York must not file the same
    review under two different weeks.
    """
    day = moment.astimezone(timezone.utc).date()
    return day - timedelta(days=day.weekday())


def last_week_start(now: datetime | None = None) -> date:
    """The most recent week that has ENDED. A review is never about a live week."""
    return week_start_of(now or datetime.now(timezone.utc)) - timedelta(weeks=1)


async def current(
    session: AsyncSession, user: User, week_start: date | None = None
) -> ReviewView:
    """One week's review, finished or not."""
    start = week_start or last_week_start()
    end = start + timedelta(weeks=1)

    view = ReviewView(
        week_start=start,
        week_end=end - timedelta(days=1),
        week_number=start.isocalendar().week,
    )

    # --- the numbers: the trades that closed inside the week ---
    trades = list(
        await session.scalars(
            select(Trade).where(
                Trade.user_id == user.id,
                Trade.closed_at.is_not(None),
                Trade.closed_at >= _at(start),
                Trade.closed_at < _at(end),
            )
        )
    )
    capitals = await trades_service.capitals_for(session, user)
    rated: list[Decimal] = []
    breaches = 0
    for trade in trades:
        summary = await note_service.trade_summary(
            session, trade, capital=capitals.get(trade.portfolio_id)
        )
        if summary.r_multiple is None:
            continue
        value = Decimal(summary.r_multiple)
        rated.append(value)
        if value < BEYOND_STOP:
            breaches += 1
    view.trades_closed = len(trades)
    view.rated_trades = len(rated)
    view.stops_held = len(rated) - breaches
    if rated:
        view.net_r = d(sum(rated, ZERO))

    # --- the journal: the notes of that week ---
    notes = list(
        await session.scalars(
            select(Note)
            .where(
                Note.user_id == user.id,
                Note.created_at >= _at(start),
                Note.created_at < _at(end),
            )
            .order_by(Note.created_at.desc())
            .limit(NOTES_SHOWN)
        )
    )
    view.notes = [
        ReviewNoteView(
            id=note.id,
            summary=note.summary,
            created_at=note.created_at,
        )
        for note in notes
    ]

    # --- the sentences ---
    mine = await session.scalar(
        select(WeeklyReview).where(
            WeeklyReview.user_id == user.id, WeeklyReview.week_start == start
        )
    )
    if mine is not None:
        view.completed = True
        view.commitment = mine.commitment

    latest = await session.scalar(
        select(WeeklyReview)
        .where(WeeklyReview.user_id == user.id)
        .order_by(WeeklyReview.week_start.desc())
        .limit(1)
    )
    if latest is not None:
        view.active_commitment = latest.commitment

    view.streak_weeks = await _streak(session, user)
    return view


async def save(
    session: AsyncSession, user: User, commitment: str, week_start: date | None = None
) -> ReviewView:
    """Finish (or re-word) one week's review.

    An upsert on (user, week): finishing the same week twice edits its sentence,
    because the trader may want a better one and there is nothing else to keep.
    """
    start = week_start or last_week_start()
    text = commitment.strip()

    row = await session.scalar(
        select(WeeklyReview).where(
            WeeklyReview.user_id == user.id, WeeklyReview.week_start == start
        )
    )
    if row is None:
        session.add(WeeklyReview(user_id=user.id, week_start=start, commitment=text))
    else:
        row.commitment = text
    await session.commit()

    return await current(session, user, week_start=start)


async def _streak(session: AsyncSession, user: User) -> int:
    """Consecutive finished weeks, ending at the most recent review.

    Walked backwards from the newest row, the way the note streak is walked from
    today: a gap ends the count, and a missing week is not silently skipped.
    """
    weeks = list(
        await session.scalars(
            select(WeeklyReview.week_start)
            .where(WeeklyReview.user_id == user.id)
            .order_by(WeeklyReview.week_start.desc())
        )
    )
    if not weeks:
        return 0
    streak = 1
    for previous, older in zip(weeks, weeks[1:]):
        if previous - older != timedelta(weeks=1):
            break
        streak += 1
    return streak


def _at(day: date) -> datetime:
    """Midnight UTC on a date, the boundary every week is cut on."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
