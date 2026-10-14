"""Trade reads: the summary list and the full timeline of one trade.

Every number is derived from the change rows at read time.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import position as pos
from app.domain.errors import TradeNotFoundError
from app.models import Change, Note, Portfolio, Prediction, Trade, User
from app.schemas.api import TradeDetailView, TradeSummaryView, PredictionView, d
from app.services import notes as note_service


async def prediction_view(
    session: AsyncSession, trade_id: UUID
) -> PredictionView | None:
    """What the trader expected after closing this trade, and whether it happened.

    Its own read, not part of the summary: a prediction exists only for a closed
    trade, and it is not a number derived from the changes like the rest.
    """
    prediction = await session.scalar(
        select(Prediction).where(Prediction.trade_id == trade_id)
    )
    if prediction is None:
        return None
    return PredictionView(
        level=d(prediction.level),
        direction=prediction.direction,
        reason=prediction.reason,
        result=prediction.result,
        resolved_at=prediction.resolved_at,
    )


async def capitals_for(session: AsyncSession, user: User) -> dict:
    """Every portfolio's capital for this trader, in one query.

    A trade is sized against its own market's money, so a list that spans
    portfolios is still correct — and one query covers the whole list.
    """
    rows = await session.execute(
        select(Portfolio.id, Portfolio.capital).where(Portfolio.user_id == user.id)
    )
    return dict(rows.all())


async def list_trades(
    session: AsyncSession,
    user: User,
    *,
    open_only: bool = False,
    limit: int = 200,
    portfolio_id: UUID | None = None,
) -> list[TradeSummaryView]:
    query = select(Trade).where(Trade.user_id == user.id)
    if portfolio_id is not None:
        query = query.where(Trade.portfolio_id == portfolio_id)
    if open_only:
        query = query.where(Trade.closed_at.is_(None))
    rows = await session.scalars(
        query.order_by(Trade.closed_at.is_(None).desc(), Trade.opened_at.desc()).limit(limit)
    )
    capitals = await capitals_for(session, user)
    return [
        await note_service.trade_summary(
            session, trade, capital=capitals.get(trade.portfolio_id)
        )
        for trade in rows
    ]


async def get_trade(
    session: AsyncSession, user: User, trade_id: UUID, storage=None
) -> TradeDetailView:
    trade = await session.scalar(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id)
    )
    if trade is None:
        raise TradeNotFoundError("Trade not found")

    summary = await note_service.trade_summary(
        session, trade, capital=(await capitals_for(session, user)).get(trade.portfolio_id)
    )
    changes = await note_service.changes_for_trade(session, trade.id)
    timeline = await note_service.notes_for_trade(session, trade.id, storage)
    return TradeDetailView(
        **summary.model_dump(),
        notes=timeline,
        warnings=pos.warnings(changes, closed=trade.closed_at is not None),
        prediction=await prediction_view(session, trade.id),
    )


async def delete_trade(
    session: AsyncSession, user: User, trade_id: UUID, storage=None
) -> None:
    """Delete a trade and everything recorded on it. A HARD delete.

    The trader asked for this trade to be gone, so nothing about it is left
    behind: the notes he recorded on it, the change rows those notes applied,
    the prediction it carried, the trade row itself, and the media in storage.
    Nothing here is restored, and a delete cannot be undone.

    A note whose ``trade_id`` is NULL is not touched: it was never recorded
    against this trade, so it is not part of it.

    The trade row is taken with ``with_for_update()``. The applier locks the
    trade in the same order before it writes, so an approval cannot add a change
    row under a trade that is disappearing, and a delete cannot race one.

    Order matters: ``predictions`` and ``notes`` both point at ``trades``, and
    ``changes`` points at ``notes``. No foreign key cascades, so the children go
    first, deepest one first, or the delete is refused by the database.
    """
    trade = await session.scalar(
        select(Trade)
        .where(Trade.id == trade_id, Trade.user_id == user.id)
        .with_for_update()
    )
    if trade is None:
        raise TradeNotFoundError("Trade not found")

    notes = list(
        await session.scalars(
            select(Note).where(Note.trade_id == trade.id, Note.user_id == user.id)
        )
    )
    note_ids = [note.id for note in notes]

    # The media goes with the rows, best effort: the trader asked for the trade
    # to be gone, and a recording or a screenshot must not outlive it in a
    # bucket. A storage failure cannot keep the rows alive.
    if storage is not None:
        for key in [k for note in notes for k in (note.audio_key, note.image_key) if k]:
            try:
                await storage.delete(key)
            except Exception:
                pass

    await session.execute(delete(Prediction).where(Prediction.trade_id == trade.id))
    if note_ids:
        await session.execute(delete(Change).where(Change.note_id.in_(note_ids)))
        await session.execute(delete(Note).where(Note.id.in_(note_ids)))
    await session.delete(trade)
