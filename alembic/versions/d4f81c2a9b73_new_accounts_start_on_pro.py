"""users.plan: a new account starts on PRO

Revision ID: d4f81c2a9b73
Revises: c1e93b7a4d20

Pro has no price and no payment provider yet, so a new account is given every
feature from its first request (see `provision_user`).

This changes the DEFAULT only. Existing rows keep the plan they have, so an
account an operator moved down to FREE stays there.
"""

import sqlalchemy as sa
from alembic import op

revision = "d4f81c2a9b73"
down_revision = "c1e93b7a4d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "plan",
        server_default=sa.text("'PRO'"),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "plan",
        server_default=sa.text("'FREE'"),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )
