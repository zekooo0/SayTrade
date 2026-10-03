"""predictions: what the trader expected after a close

Revision ID: f1a8b3c70d94
Revises: e9c2f7a41b83

A trade has two dimensions. The trade result is derived from the changes and
already reported as wins, losses and the win rate. The prediction result is a
fact about what the price did after the close, and the app holds no prices — so
it is the one value that has to be stored, from the trader's own word.

One row per trade: a closed trade cannot be reopened, and a second position in
the same stock is a new trade.
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a8b3c70d94"
down_revision = "e9c2f7a41b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("trade_id", sa.Uuid(as_uuid=True), sa.ForeignKey("trades.id"), nullable=False),
        sa.Column("note_id", sa.Uuid(as_uuid=True), sa.ForeignKey("notes.id"), nullable=True),
        sa.Column("level", sa.Numeric(20, 8), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "result", sa.String(length=12), nullable=False, server_default="PENDING"
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_note_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("notes.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("trade_id", name="uq_predictions_trade"),
    )
    op.create_index(
        "ix_predictions_user_result", "predictions", ["user_id", "result"]
    )


def downgrade() -> None:
    op.drop_index("ix_predictions_user_result", table_name="predictions")
    op.drop_table("predictions")
