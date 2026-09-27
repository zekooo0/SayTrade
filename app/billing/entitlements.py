"""Entitlements: what a plan allows. A bounded module of its own — the trading
domain never asks whether a user is Free or Pro.

Two limits, two different jobs:

* **Active trades** are the product limit. The check runs when a note would
  OPEN a trade, and never when a note changes a trade the trader already holds:
  a journal that refuses to record a follow-up is broken.
* **AI voice notes per month** are an abuse guard. They are checked before any
  AI spend, so one account cannot burn unlimited transcription and extraction.
  A generous limit, not a product limit.

Nothing is stored as a counter. The trade count is the count of open trades and
the note count is the count of this month's voice notes, so no counter can
drift out of sync with reality.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import AllowanceExhaustedError, TradeLimitReachedError
from app.models import Note, Trade, User
from app.schemas.api import UsageView

PLAN_FREE = "FREE"
PLAN_PRO = "PRO"


@dataclass(frozen=True)
class PlanLimits:
    """The numbers behind a plan. `None` means unlimited."""

    active_trades: int | None
    voice_notes_per_month: int | None


def month_period(now: datetime) -> tuple[datetime, datetime]:
    """Calendar-month window in UTC: [first of month, first of next month)."""
    year, month = now.year, now.month
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1
    end = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    return start, end


class Entitlements:
    def __init__(
        self,
        free_active_trades: int = 3,
        free_voice_notes_per_month: int | None = 100,
        pro_active_trades: int | None = None,
        pro_voice_notes_per_month: int | None = None,
        pro_price_usd: int = 5,
        upgrade_url: str = "",
    ):
        self.limits = {
            PLAN_FREE: PlanLimits(
                active_trades=free_active_trades,
                voice_notes_per_month=free_voice_notes_per_month,
            ),
            PLAN_PRO: PlanLimits(
                active_trades=pro_active_trades,
                voice_notes_per_month=pro_voice_notes_per_month,
            ),
        }
        self.pro_price_usd = pro_price_usd
        self.upgrade_url = upgrade_url

    @classmethod
    def from_settings(cls, settings) -> "Entitlements":  # noqa: ANN001 - Settings
        """Build from config, so no code path can run without a plan policy."""
        return cls(
            free_active_trades=settings.free_active_trades,
            free_voice_notes_per_month=settings.free_voice_notes_per_month,
            pro_active_trades=settings.pro_active_trades,
            pro_voice_notes_per_month=settings.pro_voice_notes_per_month,
            pro_price_usd=settings.pro_price_usd,
            upgrade_url=settings.upgrade_url,
        )

    # --- plan ---

    def plan_for(self, user: User) -> str:
        """The stored plan. Unknown values fall back to FREE, never to PRO."""
        plan = getattr(user, "plan", None) or PLAN_FREE
        return plan if plan in self.limits else PLAN_FREE

    def limits_for(self, plan: str) -> PlanLimits:
        return self.limits.get(plan, self.limits[PLAN_FREE])

    def _upgrade_hint(self) -> str:
        if self.upgrade_url:
            return f" Upgrade to Pro (${self.pro_price_usd}/month): {self.upgrade_url}"
        return f" Upgrade to Pro (${self.pro_price_usd}/month) to hold more."

    # --- counts ---

    async def active_trades_used(self, session: AsyncSession, user_id: UUID) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Trade)
                .where(Trade.user_id == user_id, Trade.closed_at.is_(None))
            )
            or 0
        )

    async def voice_notes_used(
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

    # --- the two checks ---

    async def reserve_voice(
        self, session: AsyncSession, user: User, now: datetime | None = None
    ) -> None:
        """402 when this month's voice-note guard is spent. Runs before any AI spend."""
        now = now or datetime.now(timezone.utc)
        limit = self.limits_for(self.plan_for(user)).voice_notes_per_month
        if limit is None:
            return
        used = await self.voice_notes_used(session, user.id, now)
        if used >= limit:
            _, end = month_period(now)
            raise AllowanceExhaustedError(
                f"You have used {used} of {limit} AI voice notes this month. "
                f"The count resets on {end.date().isoformat()}. "
                "Typed notes still work and are always free."
            )

    async def check_can_open_trade(
        self, session: AsyncSession, user: User
    ) -> None:
        """402 when the plan's active-trade limit is reached.

        Called only when a note would create a new trade.
        """
        plan = self.plan_for(user)
        limit = self.limits_for(plan).active_trades
        if limit is None:
            return
        used = await self.active_trades_used(session, user.id)
        if used >= limit:
            raise TradeLimitReachedError(
                f"Your {plan.lower()} plan holds {limit} active trades, and you hold {used}. "
                "Close a trade to open another." + self._upgrade_hint()
            )

    # --- the view ---

    async def usage(self, session: AsyncSession, user: User) -> UsageView:
        now = datetime.now(timezone.utc)
        start, end = month_period(now)
        plan = self.plan_for(user)
        limits = self.limits_for(plan)
        return UsageView(
            plan=plan,
            active_trades_used=await self.active_trades_used(session, user.id),
            active_trades_limit=limits.active_trades,
            voice_notes_used=await self.voice_notes_used(session, user.id, now),
            voice_notes_limit=limits.voice_notes_per_month,
            period_start=start,
            period_end=end,
        )

    def entitlements_view(self) -> dict[str, object]:
        """What the client needs to show the plan honestly."""
        return {
            "free_active_trades": self.limits[PLAN_FREE].active_trades,
            "pro_active_trades": self.limits[PLAN_PRO].active_trades,
            "pro_price_usd": self.pro_price_usd,
            "upgrade_url": self.upgrade_url,
        }
