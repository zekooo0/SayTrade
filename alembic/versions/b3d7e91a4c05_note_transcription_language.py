"""notes.transcription_language: what the model heard

Revision ID: b3d7e91a4c05
Revises: f18a6b3d92c4

gpt-transcribe returns the languages it detected. Recording it means the app
never has to guess whether a note was Arabic or English — a guess is what let an
Egyptian note come back with a Russian ticker.
"""

import sqlalchemy as sa
from alembic import op

revision = "b3d7e91a4c05"
down_revision = "f18a6b3d92c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("transcription_language", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notes", "transcription_language")
