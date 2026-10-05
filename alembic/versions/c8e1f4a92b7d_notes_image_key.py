"""notes.image_key: a screenshot can be a note

Revision ID: c8e1f4a92b7d
Revises: a4d9e2b71c58
"""

import sqlalchemy as sa

from alembic import op

revision = "c8e1f4a92b7d"
down_revision = "a4d9e2b71c58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A note can now come from a screenshot of a broker screen, instead of a
    # recording or typed words. The picture is stored under its own key rather
    # than under `audio_key`: a column named for audio that holds an image makes
    # every later reader wrong. `mime_type` and `byte_size` already describe
    # whichever media key is set, so those 2 are shared.
    op.add_column(
        "notes",
        sa.Column("image_key", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notes", "image_key")
