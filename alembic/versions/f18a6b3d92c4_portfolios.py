"""portfolios, and trades.portfolio_id: one market per trader

Revision ID: f18a6b3d92c4
Revises: e7b2c9d41f58

A portfolio is a market: it owns the currency and the capital, and every trade
in it belongs to that market. The capital moves off the account — money is
sized per market, not per account.

Backfill: each trader who has a capital or any trade gets one portfolio named
"Main", holding their old capital and their existing trades. Nothing is
invented and nothing is mixed. The currency is left NULL on purpose: the
migration does not know whether those trades are Egyptian or American, and a
guessed currency would silently mis-size every position in it.
"""

import sqlalchemy as sa
from alembic import op

revision = "f18a6b3d92c4"
down_revision = "e7b2c9d41f58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("capital", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_portfolios_user_created", "portfolios", ["user_id", "created_at"])

    op.add_column(
        "trades",
        sa.Column(
            "portfolio_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("portfolios.id"),
            nullable=True,
        ),
    )
    # The note carries it too: a note is recorded *into* a market, and the trade
    # it opens must land in that same market. Deriving it from the trade would
    # not work — at receive time the trade does not exist yet.
    op.add_column(
        "notes",
        sa.Column(
            "portfolio_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("portfolios.id"),
            nullable=True,
        ),
    )

    # One "Main" per trader who has anything to carry over.
    op.execute(
        """
        INSERT INTO portfolios (id, user_id, name, currency, capital, created_at)
        SELECT gen_random_uuid(), u.id, 'Main', NULL, u.capital, now()
        FROM users u
        WHERE u.capital IS NOT NULL
           OR EXISTS (SELECT 1 FROM trades t WHERE t.user_id = u.id)
        """
    )
    op.execute(
        """
        UPDATE trades t
        SET portfolio_id = p.id
        FROM portfolios p
        WHERE p.user_id = t.user_id AND p.name = 'Main'
        """
    )

    op.drop_column("users", "capital")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("capital", sa.Numeric(precision=20, scale=8), nullable=True),
    )
    # Best effort: a trader's "Main" capital goes back to the account. Portfolios
    # the trader made themselves are dropped with the column below.
    op.execute(
        """
        UPDATE users u
        SET capital = p.capital
        FROM portfolios p
        WHERE p.user_id = u.id AND p.name = 'Main'
        """
    )
    op.drop_column("notes", "portfolio_id")
    op.drop_column("trades", "portfolio_id")
    op.drop_index("ix_portfolios_user_created", table_name="portfolios")
    op.drop_table("portfolios")
