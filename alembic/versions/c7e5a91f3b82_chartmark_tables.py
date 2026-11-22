"""chartmark: the charts instrument's own tables, in the one database

Revision ID: c7e5a91f3b82
Revises: 8f1e4d2c9a7b

The emitted DDL, for review before it runs:

    CREATE SCHEMA IF NOT EXISTS chartmark;

    CREATE TABLE chartmark.ohlcv_cache (
        id UUID DEFAULT uuidv7() NOT NULL,
        canonical_ticker VARCHAR(48) NOT NULL,
        timeframe VARCHAR(8) NOT NULL,
        provider VARCHAR(24) NOT NULL,
        provider_symbol VARCHAR(64) DEFAULT '' NOT NULL,
        currency VARCHAR(16),
        exchange_name VARCHAR(48),
        last_checked_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        last_candle_timestamp BIGINT NOT NULL,
        candles JSONB NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT ohlcv_cache_pkey PRIMARY KEY (id),
        CONSTRAINT uq_ohlcv_cache_ticker_timeframe UNIQUE (canonical_ticker, timeframe)
    );

    CREATE INDEX ix_ohlcv_cache_ticker_timeframe
        ON chartmark.ohlcv_cache (canonical_ticker, timeframe);

    CREATE TABLE chartmark.ai_briefs (
        id UUID DEFAULT uuidv7() NOT NULL,
        user_id UUID NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT ai_briefs_pkey PRIMARY KEY (id),
        CONSTRAINT ai_briefs_user_id_fkey FOREIGN KEY (user_id)
            REFERENCES users (id) ON DELETE NO ACTION
    );

    CREATE INDEX ix_ai_briefs_user_id_created_at
        ON chartmark.ai_briefs (user_id, created_at DESC);

One row per AI brief the studio generated — the month's usage is a COUNT of
rows, so no counter can drift. The cache row is the provider bookkeeping for
completed bars, the same semantics the Next.js local-JSON cache had, except
this one survives cold starts. FK rule as everywhere in this schema: NO
ACTION.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c7e5a91f3b82"
down_revision = "8f1e4d2c9a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS chartmark;")

    op.create_table(
        "ohlcv_cache",
        sa.Column(
            "id", sa.Uuid(as_uuid=True), server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("canonical_ticker", sa.String(length=48), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column(
            "provider_symbol", sa.String(length=64),
            server_default=sa.text("''"), nullable=False,
        ),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("exchange_name", sa.String(length=48), nullable=True),
        sa.Column(
            "last_checked_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("last_candle_timestamp", sa.BigInteger, nullable=False),
        sa.Column("candles", JSONB, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canonical_ticker", "timeframe",
            name="uq_ohlcv_cache_ticker_timeframe",
        ),
        schema="chartmark",
    )
    op.create_index(
        "ix_ohlcv_cache_ticker_timeframe",
        "ohlcv_cache",
        ["canonical_ticker", "timeframe"],
        schema="chartmark",
    )

    op.create_table(
        "ai_briefs",
        sa.Column(
            "id", sa.Uuid(as_uuid=True), server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="chartmark",
    )
    op.create_index(
        "ix_ai_briefs_user_id_created_at",
        "ai_briefs",
        ["user_id", sa.text("created_at DESC")],
        schema="chartmark",
    )


def downgrade() -> None:
    op.drop_table("ai_briefs", schema="chartmark")
    op.drop_table("ohlcv_cache", schema="chartmark")
    op.execute("DROP SCHEMA IF EXISTS chartmark CASCADE;")
