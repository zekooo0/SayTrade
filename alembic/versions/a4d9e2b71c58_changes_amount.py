"""changes.amount: the money the trader named instead of a share count

Revision ID: a4d9e2b71c58
Revises: f1a8b3c70d94
"""

import sqlalchemy as sa

from alembic import op

revision = "a4d9e2b71c58"
down_revision = "f1a8b3c70d94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A trader may size a position by what he spent rather than by a share count
    # ("3000 egp at 10"). The rows still carry the shares that money bought, and
    # this keeps what he actually said: once the count is rounded down to a whole
    # share, the amount can no longer be recovered from it.
    op.add_column(
        "changes",
        sa.Column("amount", sa.Numeric(20, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("changes", "amount")
