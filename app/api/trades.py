"""Trade endpoints: the position list and one trade's full timeline."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.domain.errors import DomainError
from app.models import User
from app.schemas.api import TradeDetailView, TradeSummaryView
from app.services import trades as trade_service

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("", response_model=list[TradeSummaryView])
async def list_trades(
    open_only: bool = False,
    portfolio_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """The trader's trades. ``portfolio_id`` scopes the list to one market, which
    is what the switch in the app sends."""
    return await trade_service.list_trades(
        session, user, open_only=open_only, portfolio_id=portfolio_id
    )


@router.get("/{trade_id}", response_model=TradeDetailView)
async def get_trade(
    trade_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        return await trade_service.get_trade(
            session, user, trade_id, request.app.state.storage
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{trade_id}", status_code=204)
async def delete_trade(
    trade_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Delete a trade for good: its notes, its changes, its prediction, its media.

    A HARD delete with no undo. The trader is warned in the UI before the call
    is made.
    """
    try:
        await trade_service.delete_trade(
            session, user, trade_id, request.app.state.storage
        )
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
