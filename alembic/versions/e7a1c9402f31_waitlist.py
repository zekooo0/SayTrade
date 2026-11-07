"""waitlist: the landing page's email list

Revision ID: e7a1c9402f31
Revises: d5e2c7a1b930

The emitted DDL, for review before it runs:

    CREATE TABLE waitlist (
        id UUID DEFAULT uuidv7() NOT NULL,
        email VARCHAR(254) NOT NULL,
        source VARCHAR(32) DEFAULT 'landing' NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT waitlist_pkey PRIMARY KEY (id),
        CONSTRAINT uq_waitlist_email UNIQUE (email),
        CONSTRAINT ck_waitlist_email_shape CHECK (position('@' in email) > 1)
    );

No foreign key to users on purpose: a signup is not an account, and an address
must survive an account deletion without being tied to it.
"""

from alembic import op
import sqlalchemy as sa

revision = "e7a1c9402f31"
down_revision = "d5e2c7a1b930"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default=sa.text("'landing'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="waitlist_pkey"),
        # One row per address. This is what makes a double submit harmless.
        sa.UniqueConstraint("email", name="uq_waitlist_email"),
        # A second, cheaper guard: a value that is not an address at all never
        # reaches the table, whatever a client sends.
        sa.CheckConstraint("position('@' in email) > 1", name="ck_waitlist_email_shape"),
    )


def downgrade() -> None:
    op.drop_table("waitlist")
