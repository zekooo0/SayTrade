"""Analytics endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import AnalyticsView
from app.services.analytics import summary

router = APIRouter(tags=["analytics"])


@router.get("/analytics/summary", response_model=AnalyticsView)
async def analytics_summary(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await summary(session, user)