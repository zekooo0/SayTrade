"""Portfolios over HTTP: the switch in the app reads and writes these."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import PortfolioIn, PortfolioView
from app.services import portfolios as portfolio_service

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioView])
async def list_portfolios(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await portfolio_service.list_portfolios(session, user)


@router.post("", response_model=PortfolioView, status_code=201)
async def create_portfolio(
    payload: PortfolioIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """A new market. It starts with no trades, and the dialog that creates it
    asks for the capital in the same breath."""
    return await portfolio_service.create_portfolio(
        session,
        user,
        name=payload.name or "Untitled",
        currency=payload.currency,
        exchange=payload.exchange,
        capital=payload.capital,
    )


@router.patch("/{portfolio_id}", response_model=PortfolioView)
async def update_portfolio(
    portfolio_id: UUID,
    payload: PortfolioIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Rename it, set its currency, or set its capital.

    An absent field is left alone. Send ``clear_capital`` to unset the capital —
    a null on its own cannot mean both "leave it" and "clear it".
    """
    return await portfolio_service.update_portfolio(
        session,
        user,
        portfolio_id,
        name=payload.name,
        currency=payload.currency,
        capital=payload.capital,
        clear_capital=payload.clear_capital,
    )
