"""Trade endpoints: list, detail, and the manual journaling path."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.domain.errors import (
    DomainError,
    MultiTradeNoteError,
    OutcomeExistsError,
    PositionViolationError,
    StaleValueError,
    TradeCompletedError,
    TradeNotFoundError,
)
from app.models import User
from app.schemas.actions import ActionsPayload
from app.schemas.api import ApprovalResultView, TradeDetailView, TradeSummaryView
from app.services.trades import get_trade, list_trades, manual_apply

router = APIRouter(tags=["trades"])


@router.get("/trades", response_model=list[TradeSummaryView])
async def list_all(
    status: Literal["ACTIVE", "COMPLETED"] | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await list_trades(session, user, status)


@router.get("/trades/{trade_id}", response_model=TradeDetailView)
async def detail(
    trade_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        return await get_trade(session, user, trade_id)
    except TradeNotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/trades/manual", response_model=ApprovalResultView)
async def manual(
    payload: ActionsPayload,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Manual journaling: the same atomic action engine, no journal entry and
    no AI allowance consumed. The form submit IS the approval."""
    try:
        return await manual_apply(session, user, payload)
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc