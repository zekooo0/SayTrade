"""auth_sub: the users subject column holds a Firebase uid

Revision ID: d5e2c7a1b930
Revises: b4e6d19a75c3

The column is renamed, not dropped and recreated: the rows keep their data and
the unique index keeps its job. The name had to change because it stated a
provider, and the provider changed — a column called `clerk_sub` holding a
Firebase uid is a lie the next reader has to disprove.

Existing rows still carry old provider subjects. They are unreachable, not
harmful: a Firebase sign-in provisions a fresh row. Wiping them is a separate,
explicit action — never hide a DELETE inside a schema migration.
"""

from alembic import op

revision = "d5e2c7a1b930"
down_revision = "b4e6d19a75c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users RENAME COLUMN clerk_sub TO auth_sub")
    op.execute("ALTER TABLE users RENAME CONSTRAINT uq_users_clerk_sub TO uq_users_auth_sub")
    op.execute("ALTER TABLE users ALTER COLUMN auth_sub TYPE varchar(128)")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN auth_sub TYPE varchar(64)")
    op.execute("ALTER TABLE users RENAME CONSTRAINT uq_users_auth_sub TO uq_users_clerk_sub")
    op.execute("ALTER TABLE users RENAME COLUMN auth_sub TO clerk_sub")
