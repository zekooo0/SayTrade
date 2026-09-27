"""users.plan: the stored plan (FREE or PRO)

Revision ID: c1e93b7a4d20
Revises: b7d41c0f9e22

The plan is stored, not derived, so it can be switched by hand until a payment
provider exists. Existing rows take the server default FREE. The CHECK keeps
the allowed set honest: unknown values would silently fall back to FREE in the
entitlement code, which is the wrong direction for a billing column.
"""

import sqlalchemy as sa
from alembic import op

revision = "c1e93b7a4d20"
down_revision = "b7d41c0f9e22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "plan",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'FREE'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_plan_allowed", "users", "plan IN ('FREE', 'PRO')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_plan_allowed", "users", type_="check")
    op.drop_column("users", "plan")
