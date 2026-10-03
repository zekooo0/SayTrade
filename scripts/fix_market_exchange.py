"""Give the production markets their exchange.

The migration that added portfolios.exchange derived it from the currency, and
these markets had no currency either — so the column stayed null, and the symbol
lookup had no market to scope itself to.

The evidence for EGX is the traders' own trades: ETEL (Telecom Egypt) and SWDY
(Elsewedy Electric) are Egyptian listings. The service sets the currency too, so
EGX implies EGP.

Run against production:

    DATABASE_URL=<neon> ./.venv/bin/python scripts/fix_market_exchange.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Portfolio, User  # noqa: E402
from app.services import portfolios as portfolio_service  # noqa: E402


async def main() -> None:
    async with SessionLocal() as session:
        rows = list(await session.scalars(select(Portfolio)))
        for portfolio in rows:
            if portfolio.exchange is not None:
                print(f"  {portfolio.name!r}: already {portfolio.exchange}, left alone")
                continue
            user = await session.get(User, portfolio.user_id)
            view = await portfolio_service.update_portfolio(
                session, user, portfolio.id, exchange="EGX"
            )
            print(
                f"  {portfolio.name!r} -> exchange={view.exchange} currency={view.currency}"
            )

        print()
        for portfolio in await session.scalars(select(Portfolio)):
            print(
                f"  now: {portfolio.name!r} exchange={portfolio.exchange!r} "
                f"currency={portfolio.currency!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
