"""Identity and allowance: who this user is and how much AI voice is left."""

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
    plan = entitlements.plan_for(user)
    return MeView(
        user_id=user.id,
        plan=plan,
        usage=usage,
        entitlements={"voice_notes_per_month": usage.ai_voice_limit},
    )
