"""Trade reads: the summary list and the full timeline of one trade.

Every number is derived from the change rows at read time.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import position as pos
from app.domain.errors import TradeNotFoundError
from app.models import Trade, User
from app.schemas.api import TradeDetailView, TradeSummaryView
from app.services import notes as note_service


async def list_trades(
    session: AsyncSession, user: User, *, open_only: bool = False, limit: int = 200
) -> list[TradeSummaryView]:
    query = select(Trade).where(Trade.user_id == user.id)
    if open_only:
        query = query.where(Trade.closed_at.is_(None))
    rows = await session.scalars(
        query.order_by(Trade.closed_at.is_(None).desc(), Trade.opened_at.desc()).limit(limit)
    )
    return [await note_service.trade_summary(session, trade) for trade in rows]


async def get_trade(
    session: AsyncSession, user: User, trade_id: UUID, storage=None
) -> TradeDetailView:
    trade = await session.scalar(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id)
    )
    if trade is None:
        raise TradeNotFoundError("Trade not found")

    summary = await note_service.trade_summary(session, trade)
    changes = await note_service.changes_for_trade(session, trade.id)
    timeline = await note_service.notes_for_trade(session, trade.id, storage)
    return TradeDetailView(
        **summary.model_dump(),
        notes=timeline,
        warnings=pos.warnings(changes, closed=trade.closed_at is not None),
    )
