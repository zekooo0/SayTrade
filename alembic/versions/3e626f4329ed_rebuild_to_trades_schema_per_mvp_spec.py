"""rebuild to trades schema per MVP spec

Replaces the Opportunity-era scaffold (opportunities, opportunity_outcomes,
and the opportunity_id FKs) with the MVP spec's six tables: users, trades,
executions, management_decisions, journal_entries, trade_outcomes.

The old tables are empty (verified before this migration was written), so the
rebuild drops them outright instead of ALTERing columns in place. This keeps
the schema byte-for-byte equal to the models, named constraints everywhere,
and avoids the unnamed-FK and missing-CHECK traps of autogenerate's in-place
diff (autogenerate cannot detect CHECK constraints on altered tables).

Revision ID: 3e626f4329ed
Revises: f9717f3a4628
Create Date: 2026-09-14 23:46:03.880356

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3e626f4329ed'
down_revision: Union[str, Sequence[str], None] = 'f9717f3a4628'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the Opportunity-era tables, create the MVP spec's six tables."""
    # --- drop old schema, children before parents ---
    op.drop_table('opportunity_outcomes')
    op.drop_table('journal_entries')
    op.drop_table('management_decisions')
    op.drop_table('executions')
    op.drop_table('opportunities')

    # --- users ---
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_users'),
    )

    # --- trades ---
    op.create_table(
        'trades',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('pattern', sa.String(length=50), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('failure_point', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('target_price', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        # The one constraint the spec mandates for trades:
        #   status = COMPLETED  <->  completed_at IS NOT NULL
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name='ck_trades_status_completed_at',
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_trades_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_trades'),
    )
    op.create_index('ix_trades_user_id_status', 'trades', ['user_id', 'status'], unique=False)
    op.create_index(
        'ix_trades_user_id_opened_at', 'trades', ['user_id', sa.text('opened_at DESC')], unique=False
    )

    # --- trade_outcomes ---
    op.create_table(
        'trade_outcomes',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('trade_id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.SmallInteger(), nullable=True),
        sa.Column('result', sa.String(length=20), nullable=False),
        sa.Column('lesson', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        # Nullable, but when present it must be 1-4; NULL passes a CHECK.
        sa.CheckConstraint('type BETWEEN 1 AND 4', name='ck_trade_outcomes_type'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], name='fk_trade_outcomes_trade_id'),
        sa.PrimaryKeyConstraint('id', name='pk_trade_outcomes'),
        # One outcome per trade; doubles as the FK index.
        sa.UniqueConstraint('trade_id', name='uq_trade_outcomes_trade_id'),
    )

    # --- journal_entries ---
    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('trade_id', sa.Uuid(), nullable=True),
        sa.Column('type', sa.String(length=20), server_default=sa.text("'VOICE'"), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('audio_storage_key', sa.Text(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('byte_size', sa.BigInteger(), nullable=False),
        sa.Column('checksum', sa.String(length=128), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('proposal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('approved_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('transcription_provider', sa.String(length=100), nullable=True),
        sa.Column('transcription_model', sa.String(length=100), nullable=True),
        sa.Column('extraction_provider', sa.String(length=100), nullable=True),
        sa.Column('extraction_model', sa.String(length=100), nullable=True),
        sa.Column('failed_stage', sa.String(length=30), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('duration_ms > 0', name='ck_journal_entries_duration_positive'),
        sa.CheckConstraint('byte_size > 0', name='ck_journal_entries_byte_size_positive'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_journal_entries_user_id'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], name='fk_journal_entries_trade_id'),
        sa.PrimaryKeyConstraint('id', name='pk_journal_entries'),
    )
    op.create_index('ix_journal_entries_trade_id', 'journal_entries', ['trade_id'], unique=False)
    op.create_index('ix_journal_entries_user_id', 'journal_entries', ['user_id'], unique=False)

    # --- executions ---
    op.create_table(
        'executions',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('trade_id', sa.Uuid(), nullable=False),
        sa.Column('side', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('price', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_executions_quantity_positive'),
        sa.CheckConstraint('price > 0', name='ck_executions_price_positive'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], name='fk_executions_trade_id'),
        sa.PrimaryKeyConstraint('id', name='pk_executions'),
    )
    op.create_index('ix_executions_trade_id', 'executions', ['trade_id'], unique=False)

    # --- management_decisions ---
    op.create_table(
        'management_decisions',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('trade_id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.String(length=30), nullable=False),
        sa.Column('old_value', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('new_value', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], name='fk_management_decisions_trade_id'),
        sa.PrimaryKeyConstraint('id', name='pk_management_decisions'),
    )
    op.create_index(
        'ix_management_decisions_trade_id', 'management_decisions', ['trade_id'], unique=False
    )


def downgrade() -> None:
    """Restore the Opportunity-era scaffold (all tables were empty)."""
    # --- drop the MVP schema, children before parents ---
    op.drop_table('management_decisions')
    op.drop_table('executions')
    op.drop_table('journal_entries')
    op.drop_table('trade_outcomes')
    op.drop_table('trades')
    op.drop_table('users')

    # --- recreate the old scaffold exactly as the previous migrations built it ---
    op.create_table(
        'opportunities',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('pattern', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('neckline', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('failure_point', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('target_price', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('identified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("direction IN ('LONG', 'SHORT')", name='ck_opportunities_direction'),
        sa.CheckConstraint(
            "status IN ('IDENTIFIED', 'ENTERED', 'COMPLETED', 'ABANDONED')",
            name='ck_opportunities_status',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_opportunities'),
    )

    op.create_table(
        'executions',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('opportunity_id', sa.Uuid(), nullable=False),
        sa.Column('side', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('price', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name='ck_executions_side'),
        sa.CheckConstraint('quantity > 0', name='ck_executions_quantity_positive'),
        sa.CheckConstraint('price > 0', name='ck_executions_price_positive'),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name='fk_executions_opportunity_id'),
        sa.PrimaryKeyConstraint('id', name='pk_executions'),
    )

    op.create_table(
        'management_decisions',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('opportunity_id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.String(length=30), nullable=False),
        sa.Column('old_value', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('new_value', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name='fk_management_decisions_opportunity_id'),
        sa.PrimaryKeyConstraint('id', name='pk_management_decisions'),
    )

    op.create_table(
        'opportunity_outcomes',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('opportunity_id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.SmallInteger(), nullable=True),
        sa.Column('realized_pnl', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('realized_r_multiple', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('result', sa.String(length=20), nullable=False),
        sa.Column('lesson', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("result IN ('WIN', 'LOSS', 'BREAKEVEN')", name='ck_opportunity_outcomes_result'),
        sa.CheckConstraint('type BETWEEN 1 AND 4', name='ck_opportunity_outcomes_type'),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name='fk_opportunity_outcomes_opportunity_id'),
        sa.PrimaryKeyConstraint('id', name='pk_opportunity_outcomes'),
        sa.UniqueConstraint('opportunity_id', name='uq_opportunity_outcomes_opportunity_id'),
    )

    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('opportunity_id', sa.Uuid(), nullable=True),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('audio_url', sa.Text(), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("type IN ('VOICE', 'TEXT')", name='ck_journal_entries_type'),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name='fk_journal_entries_opportunity_id'),
        sa.PrimaryKeyConstraint('id', name='pk_journal_entries'),
    )