"""users.capital: the account's total capital

Revision ID: e7b2c9d41f58
Revises: d4f81c2a9b73

The trader sets it once. It is what turns a position's cost into a percent of
the account, so two trades of different sizes become comparable, and analytics
can reason about exposure instead of raw money.

Nullable: every other number works without it. No currency column — the value
is unitless for now, and a percent of capital needs no unit.
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b2c9d41f58"
down_revision = "d4f81c2a9b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("capital", sa.Numeric(precision=20, scale=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "capital")
