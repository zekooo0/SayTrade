"""Entitlements and allowance accounting. A separate bounded module: the
trading domain never knows whether a user is Free or Pro (monetization spec).

Until billing exists every user is FREE. The unit of allowance is the AI voice
entry; the counter lives on usage_periods and every mutation is serialized
with SELECT ... FOR UPDATE so two simultaneous recordings can never both pass
an exhausted limit. Retries never reserve again; a FAILED entry is refunded.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import AllowanceExhaustedError
from app.models import UsagePeriod, User
from app.schemas.api import UsageView

PLAN_FREE = "FREE"
PLAN_PRO = "PRO"


def month_period(now: datetime) -> tuple[datetime, datetime]:
    """Calendar-month window in UTC: [first of month, first of next month).

    Free users are accounted on a calendar month (the billing period arrives
    with subscriptions later).
    """
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

    async def _get_or_create_locked(
        self, session: AsyncSession, user_id: UUID, now: datetime | None = None
    ) -> UsagePeriod:
        """The current period row, locked for update; created lazily.

        The limit is snapshotted from configuration at creation, so pricing
        changes never need a migration. The user-period unique constraint
        resolves the concurrent-creation race via a savepoint rollback and a
        re-read.
        """
        now = now or datetime.now(timezone.utc)
        start, end = month_period(now)

        async def _select_locked() -> UsagePeriod | None:
            return await session.scalar(
                select(UsagePeriod)
                .where(
                    UsagePeriod.user_id == user_id,
                    UsagePeriod.period_start == start,
                    UsagePeriod.period_end == end,
                )
                .with_for_update()
            )

        period = await _select_locked()
        if period is not None:
            return period

        period = UsagePeriod(
            user_id=user_id,
            period_start=start,
            period_end=end,
            ai_voice_limit=self.free_voice_limit,
            ai_voice_used=0,
        )
        session.add(period)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            # A concurrent request created the row first; roll back only the
            # savepoint and lock the existing row.
            period = await _select_locked()
            if period is None:  # pragma: no cover - IntegrityError implies it exists
                raise AllowanceExhaustedError("usage period could not be created")
        return period

    async def reserve(
        self, session: AsyncSession, user_id: UUID, now: datetime | None = None
    ) -> None:
        """Atomically consume one AI voice unit. Raises 402 when exhausted."""
        period = await self._get_or_create_locked(session, user_id, now)
        if period.ai_voice_used >= period.ai_voice_limit:
            raise AllowanceExhaustedError(
                f"AI voice allowance exhausted ({period.ai_voice_used}/"
                f"{period.ai_voice_limit}) for the period ending "
                f"{period.period_end.date().isoformat()}"
            )
        period.ai_voice_used += 1

    async def refund(
        self, session: AsyncSession, user_id: UUID, now: datetime | None = None
    ) -> None:
        """Return one unit on an internal processing failure (never below 0)."""
        period = await self._get_or_create_locked(session, user_id, now)
        period.ai_voice_used = max(period.ai_voice_used - 1, 0)

    async def usage(self, session: AsyncSession, user: User) -> UsageView:
        now = datetime.now(timezone.utc)
        start, end = month_period(now)
        period = await session.scalar(
            select(UsagePeriod).where(
                UsagePeriod.user_id == user.id,
                UsagePeriod.period_start == start,
                UsagePeriod.period_end == end,
            )
        )
        if period is None:
            used, limit = 0, self.free_voice_limit
        else:
            used, limit = period.ai_voice_used, period.ai_voice_limit
        return UsageView(
            plan=self.plan_for(user),
            ai_voice_limit=limit,
            ai_voice_used=used,
            ai_voice_remaining=max(limit - used, 0),
            period_start=start,
            period_end=end,
        )