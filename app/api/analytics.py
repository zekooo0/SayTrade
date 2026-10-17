"""Analytics endpoint: the scoreboard."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import Portfolio, User
from app.schemas.api import AnalyticsView
from app.services import analytics as analytics_service

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsView)
async def analytics(
    portfolio_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """The scoreboard.

    ``portfolio_id`` scopes the MONEY figures to one market — money is only
    meaningful inside one currency, and the tab shows one market at a time. The
    R statistics and the habit ignore it: they carry no currency. Without it,
    every market is added together, which is what the trades board's caller and
    the existing tests expect.
    """
    if portfolio_id is not None:
        market = await session.get(Portfolio, portfolio_id)
        if market is None or market.user_id != user.id:
            # A market id that is not yours is not found, never empty: a trader
            # must not be able to tell an empty market from somebody else's.
            raise HTTPException(status_code=404, detail="Market not found")
    return await analytics_service.overview(session, user, portfolio_id=portfolio_id)
