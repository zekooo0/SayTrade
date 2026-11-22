"""Predictions endpoint: the trader's forward calls, as one flat list.

The scoreboard answers "how often is he right?" with a count. This answers
"which ones were they?" — and the two read the same rows, so a page can never
show nine right predictions above a list that only has eight.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import Portfolio, User
from app.schemas.api import PredictionRowView
from app.services import predictions as prediction_service

router = APIRouter(tags=["predictions"])


@router.get("/predictions", response_model=list[PredictionRowView])
async def predictions(
    portfolio_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Every prediction this trader made, newest first.

    ``portfolio_id`` limits the list to one market, through the trade each
    prediction belongs to — the same scope rule the scoreboard follows. Without
    it, every market is listed.

    This is a read of rows the trader already made: there is nothing to enter
    here. A prediction is recorded by the note that closes a trade.
    """
    if portfolio_id is not None:
        market = await session.get(Portfolio, portfolio_id)
        if market is None or market.user_id != user.id:
            # A market that is not yours is not found, never empty.
            raise HTTPException(status_code=404, detail="Market not found")
    return await prediction_service.list_predictions(
        session, user, portfolio_id=portfolio_id, limit=limit
    )
