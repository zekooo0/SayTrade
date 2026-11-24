"""community: shared ChartMark setups feed, votes, and comments

Revision ID: a9b4c2d8e1f0
Revises: c7e5a91f3b82
"""

from alembic import op
import sqlalchemy as sa

revision = "a9b4c2d8e1f0"
down_revision = "c7e5a91f3b82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS chartmark;")

    op.create_table(
        "community_setups",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(length=64), nullable=False),
        sa.Column("canonical_ticker", sa.String(length=48), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("regime_status", sa.String(length=32), nullable=False),
        sa.Column(
            "price_basis",
            sa.String(length=12),
            server_default=sa.text("'wick'"),
            nullable=False,
        ),
        sa.Column("latest_close", sa.Float(), nullable=False),
        sa.Column("primary_floor_price", sa.Float(), nullable=True),
        sa.Column("primary_floor_date", sa.String(length=16), nullable=True),
        sa.Column("minor_bottom_price", sa.Float(), nullable=True),
        sa.Column("minor_bottom_date", sa.String(length=16), nullable=True),
        sa.Column("highest_peak_price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="chartmark",
    )
    op.create_index(
        "ix_community_setups_created_at",
        "community_setups",
        [sa.text("created_at DESC")],
        schema="chartmark",
    )
    op.create_index(
        "ix_community_setups_market_created_at",
        "community_setups",
        ["market", sa.text("created_at DESC")],
        schema="chartmark",
    )

    op.create_table(
        "community_setup_votes",
        sa.Column(
            "setup_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("chartmark.community_setups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("setup_id", "user_id"),
        schema="chartmark",
    )

    op.create_table(
        "community_setup_comments",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column(
            "setup_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("chartmark.community_setups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="chartmark",
    )
    op.create_index(
        "ix_community_setup_comments_setup_created_at",
        "community_setup_comments",
        ["setup_id", "created_at"],
        schema="chartmark",
    )


def downgrade() -> None:
    op.drop_table("community_setup_comments", schema="chartmark")
    op.drop_table("community_setup_votes", schema="chartmark")
    op.drop_table("community_setups", schema="chartmark")
