"""polar_subscriptions: the current provider's subscription rows

Revision ID: 8f1e4d2c9a7b
Revises: e7a1c9402f31

The emitted DDL, for review before it runs:

    CREATE TABLE polar_subscriptions (
        id UUID DEFAULT uuidv7() NOT NULL,
        user_id UUID NOT NULL,
        polar_subscription_id VARCHAR(64) NOT NULL,
        polar_customer_id VARCHAR(64),
        polar_product_id VARCHAR(64),
        billing_period VARCHAR(16),
        status VARCHAR(20) NOT NULL,
        current_period_start TIMESTAMPTZ,
        current_period_end TIMESTAMPTZ,
        cancel_at_period_end BOOLEAN DEFAULT false NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT polar_subscriptions_pkey PRIMARY KEY (id),
        CONSTRAINT uq_polar_subscriptions_polar_subscription_id UNIQUE (polar_subscription_id),
        CONSTRAINT polar_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE NO ACTION
    );

    CREATE INDEX ix_polar_subscriptions_user_id_created_at
        ON polar_subscriptions (user_id, created_at DESC);

Same contract as the LemonSqueezy `subscriptions` table, which stays where it
is: the row holds the provider's ids and the state it reports, the PLAN stays
on `users.plan`, and nothing here decides access. The FK rule is NO ACTION —
a subscription is never cascaded away by a user row.
"""

from alembic import op
import sqlalchemy as sa

revision = "8f1e4d2c9a7b"
down_revision = "e7a1c9402f31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "polar_subscriptions",
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
        sa.Column("polar_subscription_id", sa.String(length=64), nullable=False),
        sa.Column("polar_customer_id", sa.String(length=64), nullable=True),
        sa.Column("polar_product_id", sa.String(length=64), nullable=True),
        sa.Column("billing_period", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
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
            "polar_subscription_id",
            name="uq_polar_subscriptions_polar_subscription_id",
        ),
    )
    op.create_index(
        "ix_polar_subscriptions_user_id_created_at",
        "polar_subscriptions",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("polar_subscriptions")
