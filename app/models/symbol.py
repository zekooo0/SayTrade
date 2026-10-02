"""The ticker reference. Vendored from the free-ticker-database project (MIT).

The extractor is no longer asked to know tickers. It returns the company name it
heard, and this table answers "which ticker is that, in this market?" — so a
ticker from another exchange cannot be produced by a confident guess.

`market` is the product's market, not the venue: "EGX" or "US". A US listing can
sit on NASDAQ, NYSE, NYSE ARCA, NYSE MKT or BATS, so the venue is kept on the row
but never queried.
"""

import uuid

from sqlalchemy import Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    ticker: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str] = mapped_column(String(24), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(24), nullable=True)

    __table_args__ = (UniqueConstraint("market", "ticker", name="uq_symbols_market_ticker"),)


class SymbolAlias(Base):
    """Every name a company is known by, normalised, for the lookup.

    The upstream database publishes 60k+ name rows; only the ones for symbols we
    seeded are kept. `alias` is stored normalised (lowercase, single-spaced) so
    the lookup is one index hit and no cleaning happens at query time.
    """

    __tablename__ = "symbol_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    ticker: Mapped[str] = mapped_column(String(24), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        Index("ix_symbol_aliases_lookup", "market", "alias"),
        Index("ix_symbol_aliases_ticker", "market", "ticker"),
    )
