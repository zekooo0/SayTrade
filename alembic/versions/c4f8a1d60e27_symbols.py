"""symbols, symbol_aliases, and the market's exchange

Revision ID: c4f8a1d60e27
Revises: b3d7e91a4c05

The ticker lookup stops being the model's job. `symbols` holds the reference for
the two markets the product offers (EGX, US), seeded from the vendored
free-ticker-database export; `symbol_aliases` holds every name each company is
known by, so a spoken name can be matched without asking a model.

`portfolios.exchange` says which of the two markets a portfolio is. The existing
rows are backfilled from their currency: EGP is the EGX, USD is the US market.
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f8a1d60e27"
down_revision = "b3d7e91a4c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=24), nullable=False),
        sa.Column("isin", sa.String(length=24), nullable=True),
        sa.UniqueConstraint("market", "ticker", name="uq_symbols_market_ticker"),
    )

    op.create_table(
        "symbol_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=24), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
    )
    op.create_index(
        "ix_symbol_aliases_lookup", "symbol_aliases", ["market", "alias"]
    )
    op.create_index(
        "ix_symbol_aliases_ticker", "symbol_aliases", ["market", "ticker"]
    )

    op.add_column("portfolios", sa.Column("exchange", sa.String(length=8), nullable=True))
    op.execute(
        "UPDATE portfolios SET exchange = 'EGX' WHERE upper(currency) = 'EGP' "
        "AND exchange IS NULL"
    )
    op.execute(
        "UPDATE portfolios SET exchange = 'US' WHERE upper(currency) = 'USD' "
        "AND exchange IS NULL"
    )


def downgrade() -> None:
    op.drop_column("portfolios", "exchange")
    op.drop_index("ix_symbol_aliases_ticker", table_name="symbol_aliases")
    op.drop_index("ix_symbol_aliases_lookup", table_name="symbol_aliases")
    op.drop_table("symbol_aliases")
    op.drop_table("symbols")
