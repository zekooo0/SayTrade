"""subscriptions: the row behind a paid plan, and new accounts start FREE

Revision ID: b4e6d19a75c3
Revises: c8e1f4a92b7d

Two changes in one revision, because they are one decision: a trader can now pay,
so a new account no longer starts on PRO.

The `users.plan` change is the DEFAULT only. Every existing row keeps the plan it
has, so an account that is PRO today stays PRO.

The table stores the provider's ids and the state it reports. It holds no price:
the provider owns the price, and a second copy of a number will disagree with it.
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e6d19a75c3"
down_revision = "c8e1f4a92b7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
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
        sa.Column("ls_subscription_id", sa.String(length=64), nullable=False),
        sa.Column("ls_customer_id", sa.String(length=64), nullable=True),
        sa.Column("ls_order_id", sa.String(length=64), nullable=True),
        sa.Column("variant_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("renews_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "test_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ls_subscription_id", name="uq_subscriptions_ls_subscription_id"
        ),
    )
    op.create_index(
        "ix_subscriptions_user_id_created_at",
        "subscriptions",
        ["user_id", sa.text("created_at DESC")],
    )

    op.alter_column(
        "users",
        "plan",
        server_default=sa.text("'FREE'"),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "plan",
        server_default=sa.text("'PRO'"),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_index("ix_subscriptions_user_id_created_at", table_name="subscriptions")
    op.drop_table("subscriptions")
