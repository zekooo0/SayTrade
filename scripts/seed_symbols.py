"""Load seed_data/ into the symbols and symbol_aliases tables.

Idempotent: every run replaces the rows for the markets present in the files, so
it can be re-run after `build_symbol_data.py` without duplicating anything.

    ./.venv/bin/python scripts/seed_symbols.py
"""

import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Symbol, SymbolAlias  # noqa: E402
from app.services.symbols import normalize  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "seed_data"


async def main() -> None:
    symbols = list(csv.DictReader((DATA / "symbols.csv").open(encoding="utf-8")))
    published = list(
        csv.DictReader((DATA / "symbol_aliases.csv").open(encoding="utf-8"))
    )
    if not symbols:
        raise SystemExit("seed_data/symbols.csv is empty — run build_symbol_data.py")

    market_of = {row["ticker"]: row["market"] for row in symbols}
    markets = sorted(set(market_of.values()))

    # Every name a company goes by: the published alias rows, the primary name,
    # and the normalised name the upstream export keeps in its own column.
    names: set[tuple[str, str, str]] = set()
    for row in symbols:
        for raw in (row["name"], row.get("aliases", "")):
            key = normalize(raw)
            if key:
                names.add((row["market"], row["ticker"], key))
    for row in published:
        market = market_of.get(row["ticker"])
        key = normalize(row["alias"])
        if market and key:
            names.add((market, row["ticker"], key))

    async with SessionLocal() as session:
        await session.execute(
            delete(SymbolAlias).where(SymbolAlias.market.in_(markets))
        )
        await session.execute(delete(Symbol).where(Symbol.market.in_(markets)))
        session.add_all(
            Symbol(
                market=row["market"],
                ticker=row["ticker"],
                name=row["name"],
                exchange=row["exchange"],
                isin=row.get("isin") or None,
            )
            for row in symbols
        )
        session.add_all(
            SymbolAlias(market=market, ticker=ticker, alias=alias)
            for market, ticker, alias in sorted(names)
        )
        await session.commit()

    per_market: dict[str, int] = {}
    for row in symbols:
        per_market[row["market"]] = per_market.get(row["market"], 0) + 1
    print(f"seeded {len(symbols)} symbols and {len(names)} aliases")
    for market, count in sorted(per_market.items()):
        print(f"  {market}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
