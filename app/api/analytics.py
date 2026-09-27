"""Analytics endpoint: the scoreboard."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import AnalyticsView
from app.services import analytics as analytics_service

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsView)
async def analytics(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await analytics_service.overview(session, user)
