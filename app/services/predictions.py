"""Prediction accuracy: a trade has two dimensions, kept apart.

The trade result is derived from the changes and already reported — wins, losses,
the win rate. The prediction result is a fact about what the price did after the
close, which the app cannot know, so the trader records it and it is stored.

A losing trade can carry a correct prediction. That combination is the reason
this exists, so the two numbers are never merged.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Prediction, Trade, User
from app.schemas.api import PredictionAccuracyView, d

# Under this many judged predictions the number says nothing. The analytics page
# already applies the same rule to the win rate: state the sample size, and say
# when it is too thin.
THIN_SAMPLE = 5


async def accuracy(
    session: AsyncSession, user: User, portfolio_id: UUID | None = None
) -> PredictionAccuracyView:
    """How often the trader's predictions about what happens next were right.

    PENDING is excluded from the percentage, per the product rule: a prediction
    that has not been judged yet is not evidence either way. It is still counted,
    so the page can say how many are waiting.

    ``portfolio_id`` limits the answer to one market's predictions, through the
    trade each one belongs to. Without it every prediction the trader made is
    counted, which is the unscoped view.
    """
    query = select(Prediction.result, func.count()).where(
        Prediction.user_id == user.id
    )
    if portfolio_id is not None:
        query = query.join(Trade, Trade.id == Prediction.trade_id).where(
            Trade.portfolio_id == portfolio_id
        )
    counts = dict((await session.execute(query.group_by(Prediction.result))).all())

    correct = counts.get("CORRECT", 0)
    incorrect = counts.get("INCORRECT", 0)
    pending = counts.get("PENDING", 0)
    judged = correct + incorrect

    view = PredictionAccuracyView(
        correct=correct,
        incorrect=incorrect,
        pending=pending,
        judged=judged,
        thin=judged < THIN_SAMPLE,
    )
    if judged:
        view.accuracy = round(correct / judged, 4)
    return view


async def accuracy_for_trades(
    session: AsyncSession, trade_ids: list[UUID]
) -> dict[UUID, str]:
    """The prediction result per trade, for a list of trades.

    One query for a whole list, rather than one per trade — the same reason
    capitals_for exists.
    """
    if not trade_ids:
        return {}
    rows = await session.execute(
        select(Prediction.trade_id, Prediction.result).where(
            Prediction.trade_id.in_(trade_ids)
        )
    )
    return {trade_id: result for trade_id, result in rows.all()}
