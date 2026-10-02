"""pg_trgm, for matching a company name to its spelling variants

Revision ID: d5a1c7e83b60
Revises: c4f8a1d60e27

The trader says "السويدي", the model writes "Elsewedy Electric", and the
reference says "ELSWEDY ELECTRIC". One letter apart — an exact match cannot see
it, and a substring match cannot either. Trigram similarity is what makes a
near-miss still find the company, without letting an unrelated name through.
"""

from alembic import op

revision = "d5a1c7e83b60"
down_revision = "c4f8a1d60e27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_symbol_aliases_trgm ON symbol_aliases "
        "USING gin (alias gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_symbol_aliases_trgm")
