"""Portfolios: one market each, with its own currency and capital.

A trader can have as many as they like ("Egypt", "US", "Swing"), and a trade
never crosses between them. The capital lives here, not on the account: money is
sized per market.

The market the trader picks is one of two — EGX or US — and the currency follows
from it. That is also what scopes the symbol lookup: a note recorded here can
only ever resolve to a ticker that trades here.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import PortfolioNotFoundError, UnsupportedMarketError
from app.models import Portfolio, Trade, User
from app.schemas.api import PortfolioView

# The markets the product offers, and the currency each one trades in.
# This is the whole list: the UI offers these two and the service refuses
# anything else, so a market whose symbols could never resolve cannot exist.
EXCHANGE_CURRENCY = {"EGX": "EGP", "US": "USD"}
MARKET_EXCHANGES = tuple(EXCHANGE_CURRENCY)


def currency_for(exchange: str | None) -> str | None:
    """The currency a market trades in, or None if it is not one we offer."""
    return EXCHANGE_CURRENCY.get((exchange or "").strip().upper())


def exchange_for_currency(currency: str | None) -> str | None:
    """The reverse, for markets created before the exchange was a choice."""
    wanted = (currency or "").strip().upper()
    for exchange, code in EXCHANGE_CURRENCY.items():
        if code == wanted:
            return exchange
    return None


def supported_exchange(
    *, exchange: str | None = None, currency: str | None = None
) -> str:
    """The market's exchange, or 422 if this product does not trade it.

    Either field can name the market: a client that sends ``exchange`` is the
    current shape, and one that only sends ``currency`` (the older app) still
    resolves to the same two markets. Nothing else is accepted — the symbols are
    loaded per exchange, so a third market would be a book with no tickers.
    """
    code = (exchange or "").strip().upper()
    if not code and currency:
        code = exchange_for_currency(currency) or ""
    if code not in EXCHANGE_CURRENCY:
        raise UnsupportedMarketError(
            "SayTrade offers the Egyptian Exchange (EGX) and the US market only."
        )
    return code


def portfolio_view(portfolio: Portfolio, trade_count: int = 0) -> PortfolioView:
    return PortfolioView(
        id=portfolio.id,
        name=portfolio.name,
        exchange=portfolio.exchange or exchange_for_currency(portfolio.currency),
        currency=portfolio.currency,
        capital=str(portfolio.capital) if portfolio.capital is not None else None,
        trade_count=trade_count,
        created_at=portfolio.created_at,
    )


async def get_portfolio(
    session: AsyncSession, user: User, portfolio_id: UUID
) -> Portfolio:
    """The trader's own portfolio, or 404. Ownership is part of the query, so a
    guessed id can never reach another trader's market."""
    portfolio = await session.scalar(
        select(Portfolio).where(
            Portfolio.id == portfolio_id, Portfolio.user_id == user.id
        )
    )
    if portfolio is None:
        raise PortfolioNotFoundError("Portfolio not found")
    return portfolio


async def list_portfolios(session: AsyncSession, user: User) -> list[PortfolioView]:
    portfolios = list(
        await session.scalars(
            select(Portfolio)
            .where(Portfolio.user_id == user.id)
            .order_by(Portfolio.created_at)
        )
    )
    counts = dict(
        (
            await session.execute(
                select(Trade.portfolio_id, func.count())
                .where(Trade.user_id == user.id, Trade.portfolio_id.is_not(None))
                .group_by(Trade.portfolio_id)
            )
        ).all()
    )
    return [portfolio_view(p, counts.get(p.id, 0)) for p in portfolios]


async def create_portfolio(
    session: AsyncSession,
    user: User,
    name: str,
    currency: str | None = None,
    exchange: str | None = None,
) -> PortfolioView:
    """A new market. It starts with no capital and no trades, so nothing is
    sized until the trader says how much money runs in it.

    The exchange is the choice; the currency is its consequence. A caller that
    only knows the currency (an older client) still gets a usable market.
    """
    exchange = supported_exchange(exchange=exchange, currency=currency)
    portfolio = Portfolio(
        user_id=user.id,
        name=name.strip(),
        exchange=exchange,
        currency=currency_for(exchange),
    )
    session.add(portfolio)
    await session.commit()
    return portfolio_view(portfolio)


async def update_portfolio(
    session: AsyncSession,
    user: User,
    portfolio_id: UUID,
    *,
    name: str | None = None,
    currency: str | None = None,
    exchange: str | None = None,
    capital: Decimal | None = None,
    clear_capital: bool = False,
) -> PortfolioView:
    """Rename it, change its market, or set its capital.

    ``clear_capital`` exists because ``capital=None`` cannot mean both "leave it
    alone" and "unset it" — the same reason the capital is nullable at all.
    """
    portfolio = await get_portfolio(session, user, portfolio_id)
    if name is not None:
        portfolio.name = name.strip()
    if exchange is not None:
        portfolio.exchange = supported_exchange(exchange=exchange)
        portfolio.currency = currency_for(portfolio.exchange)
    elif currency is not None:
        portfolio.exchange = supported_exchange(currency=currency)
        portfolio.currency = currency_for(portfolio.exchange)
    if clear_capital:
        portfolio.capital = None
    elif capital is not None:
        portfolio.capital = capital
    await session.commit()
    return portfolio_view(portfolio)
