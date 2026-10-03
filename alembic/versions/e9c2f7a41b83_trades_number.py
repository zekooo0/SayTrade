"""trades.number: a readable handle per trader

Revision ID: e9c2f7a41b83
Revises: d5a1c7e83b60

"#12" is a better handle for a person than a UUID. The number is per trader, not
global: a global sequence would make a new account's first trade #8,412. Existing
trades are numbered by when they opened, oldest first.

Display only. Nothing resolves a trade by this number — a note carries trade_id.
"""

import sqlalchemy as sa
from alembic import op

revision = "e9c2f7a41b83"
down_revision = "d5a1c7e83b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable first: the column has to exist before it can be filled.
    op.add_column("trades", sa.Column("number", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE trades AS t
           SET number = numbered.position
          FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY user_id
                           ORDER BY opened_at, created_at
                       ) AS position
                  FROM trades
               ) AS numbered
         WHERE t.id = numbered.id
        """
    )

    op.alter_column("trades", "number", nullable=False)
    op.create_unique_constraint(
        "uq_trades_user_number", "trades", ["user_id", "number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_trades_user_number", "trades", type_="unique")
    op.drop_column("trades", "number")
