"""Account endpoints: plan, allowance usage, entitlements."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import MeView

router = APIRouter(tags=["me"])


@router.get("/me")
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    entitlements = request.app.state.entitlements
    usage = await entitlements.usage(session, user)
    remaining = usage.ai_voice_remaining
    return MeView(
        user_id=user.id,
        plan=usage.plan,
        usage=usage,
        entitlements={
            "can_submit_voice": remaining > 0,
            "ai_voice_remaining": remaining,
            "analytics": "basic",  # "advanced" only once Pro billing exists
            "ai_journal_analysis": False,
        },
    )