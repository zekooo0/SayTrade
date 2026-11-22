"""weekly_reviews: the commitment a finished week produces

Revision ID: a3f8c21d64be
Revises: e7a1c9402f31

The emitted DDL, for review before it runs:

    CREATE TABLE weekly_reviews (
        id UUID DEFAULT uuidv7() NOT NULL,
        user_id UUID NOT NULL,
        week_start DATE NOT NULL,
        commitment TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT weekly_reviews_pkey PRIMARY KEY (id),
        CONSTRAINT fk_weekly_reviews_user FOREIGN KEY (user_id) REFERENCES users (id),
        CONSTRAINT uq_weekly_reviews_user_week UNIQUE (user_id, week_start)
    );
    CREATE INDEX ix_weekly_reviews_user_week ON weekly_reviews (user_id, week_start);

Six decisions this schema makes, each of which can be rejected:

1.  Name `weekly_reviews`. The week's numbers are not in it — they are derived
    from the change rows on read, so a review cannot disagree with the journal.
2.  `week_start DATE` is the UTC Monday of the week being reviewed, and
    (user_id, week_start) is unique. Finishing a week twice edits its sentence.
3.  `commitment TEXT NOT NULL`: a finished review always carries the one thing
    the trader will do next week. An empty box never reaches the table.
4.  No `completed_at`. The row IS the completion; `created_at` records when, and
    `updated_at` when it was edited.
5.  No streak column. Streaks are consecutive `week_start` values, counted on
    read like the note streak already is.
6.  The commitment governs the week AFTER `week_start`. Today shows the most
    recent review's sentence, which is what "your commitment this week" means.

No CHECK constraints and no cascade: every other foreign key in this schema is
NO ACTION, and the delete path for a user is a soft delete with a 30-day purge,
not a cascade.
"""

from alembic import op
import sqlalchemy as sa

revision = "a3f8c21d64be"
down_revision = "e7a1c9402f31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_reviews",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("commitment", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="weekly_reviews_pkey"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_weekly_reviews_user"
        ),
        sa.UniqueConstraint(
            "user_id", "week_start", name="uq_weekly_reviews_user_week"
        ),
    )
    op.create_index(
        "ix_weekly_reviews_user_week",
        "weekly_reviews",
        ["user_id", "week_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_reviews_user_week", table_name="weekly_reviews")
    op.drop_table("weekly_reviews")
