"""Entitlements and allowance accounting. A separate bounded module: the
trading domain never knows whether a user is Free or Pro.

The unit of allowance is the AI voice note. The counter is the count of voice
notes in the calendar month — no counter table, so nothing can drift out of
sync with reality. The check runs before any AI spend.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import AllowanceExhaustedError
from app.models import Note, User
from app.schemas.api import UsageView

PLAN_FREE = "FREE"
PLAN_PRO = "PRO"


def month_period(now: datetime) -> tuple[datetime, datetime]:
    """Calendar-month window in UTC: [first of month, first of next month)."""
    year, month = now.year, now.month
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1
    end = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    return start, end


class Entitlements:
    def __init__(self, free_voice_limit: int):
        self.free_voice_limit = free_voice_limit

    def plan_for(self, _user: User) -> str:
        return PLAN_FREE  # billing is a later module; everyone is FREE today

    async def _voice_notes_used(
        self, session: AsyncSession, user_id: UUID, now: datetime
    ) -> int:
        start, end = month_period(now)
        return (
            await session.scalar(
                select(func.count())
                .select_from(Note)
                .where(
                    Note.user_id == user_id,
                    Note.source == "VOICE",
                    Note.created_at >= start,
                    Note.created_at < end,
                )
            )
            or 0
        )

    async def reserve(
        self, session: AsyncSession, user_id: UUID, now: datetime | None = None
    ) -> None:
        """Raise 402 when this month's AI voice allowance is used up."""
        now = now or datetime.now(timezone.utc)
        used = await self._voice_notes_used(session, user_id, now)
        if used >= self.free_voice_limit:
            _, end = month_period(now)
            raise AllowanceExhaustedError(
                f"Your AI voice allowance is used up ({used}/{self.free_voice_limit}). "
                f"It resets on {end.date().isoformat()}."
            )

    async def usage(self, session: AsyncSession, user: User) -> UsageView:
        now = datetime.now(timezone.utc)
        start, end = month_period(now)
        used = await self._voice_notes_used(session, user.id, now)
        limit = self.free_voice_limit
        return UsageView(
            plan=self.plan_for(user),
            ai_voice_limit=limit,
            ai_voice_used=used,
            ai_voice_remaining=max(limit - used, 0),
            period_start=start,
            period_end=end,
        )
