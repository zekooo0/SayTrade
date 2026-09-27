"""Identity and plan: who this user is and what the plan allows."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import MeView

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeView)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    entitlements = request.app.state.entitlements
    usage = await entitlements.usage(session, user)
    return MeView(
        user_id=user.id,
        plan=usage.plan,
        usage=usage,
        entitlements=entitlements.entitlements_view(),
    )
