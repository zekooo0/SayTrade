"""Identity and plan: who this user is and what the plan allows."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.billing import PLAN_PRO
from app.config import settings
from app.db import get_session
from app.models import User
from app.schemas.api import ChartmarkUsageView, MeView
from app.services import chartmark

router = APIRouter(tags=["me"])


async def _me_view(request: Request, session: AsyncSession, user: User) -> MeView:
    """Everything the app knows about this account.

    The capital is not here: it belongs to a portfolio, because money is sized
    per market, not per account.
    """
    entitlements = request.app.state.entitlements
    usage = await entitlements.usage(session, user)
    return MeView(
        user_id=user.id,
        plan=usage.plan,
        usage=usage,
        entitlements=entitlements.entitlements_view(),
        chartmark=ChartmarkUsageView(
            ai_briefs_used=await chartmark.count_briefs_this_month(session, user.id),
            ai_briefs_limit=None
            if usage.plan == PLAN_PRO
            else settings.chartmark_free_ai_briefs_per_month,
        ),
    )


@router.get("/me", response_model=MeView)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _me_view(request, session, user)
