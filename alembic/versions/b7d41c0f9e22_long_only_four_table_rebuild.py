"""long-only rebuild: four tables (users, trades, notes, changes)

Revision ID: b7d41c0f9e22
Revises: 5d8f9a1c2b4e
Create Date: 2026-09-27

The long-only simplification: the six-table MVP becomes four.

  users    unchanged
  trades   no direction, no pattern, no status (open while closed_at is NULL)
  notes    one row per thing the trader said; also the pipeline job
  changes  one row per approved position change (six kinds)

Dropped: executions, management_decisions, trade_outcomes, journal_entries,
usage_periods (the free allowance is now the count of the month's voice notes).

Development data only: nothing is migrated. The downgrade restores the
previous revision's shape so `alembic downgrade` still works; it cannot
restore dropped rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7d41c0f9e22'
down_revision: Union[str, Sequence[str], None] = '5d8f9a1c2b4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rebuild to the long-only four-table schema."""
    op.drop_table('journal_entries')
    op.drop_table('trade_outcomes')
    op.drop_table('executions')
    op.drop_table('management_decisions')
    op.drop_table('usage_periods')
    op.drop_table('trades')

    op.create_table(
        'trades',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('closed_at IS NULL OR closed_at >= opened_at', name='ck_trades_closed_after_open'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trades_user_opened_at', 'trades', ['user_id', sa.text('opened_at DESC')], unique=False)
    op.create_index('ux_trades_one_open_per_symbol', 'trades', ['user_id', 'symbol'], unique=True, postgresql_where=sa.text('closed_at IS NULL'))

    op.create_table(
        'notes',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('trade_id', sa.Uuid(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('audio_key', sa.String(length=255), nullable=True),
        sa.Column('mime_type', sa.String(length=64), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('byte_size', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('failed_stage', sa.String(length=20), nullable=True),
        sa.Column('proposal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('transcription_provider', sa.String(length=30), nullable=True),
        sa.Column('transcription_model', sa.String(length=60), nullable=True),
        sa.Column('extraction_provider', sa.String(length=30), nullable=True),
        sa.Column('extraction_model', sa.String(length=60), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notes_user_created_at', 'notes', ['user_id', sa.text('created_at DESC')], unique=False)
    op.create_index('ix_notes_trade_id', 'notes', ['trade_id', 'created_at'], unique=False)
    op.create_index('ix_notes_allowance', 'notes', ['user_id', 'source', 'created_at'], unique=False)

    op.create_table(
        'changes',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('note_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('shares', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('price', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('stop', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('target', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('price IS NULL OR price > 0', name='ck_changes_price_positive'),
        sa.CheckConstraint('shares IS NULL OR shares > 0', name='ck_changes_shares_positive'),
        sa.CheckConstraint('stop IS NULL OR stop > 0', name='ck_changes_stop_positive'),
        sa.CheckConstraint('target IS NULL OR target > 0', name='ck_changes_target_positive'),
        sa.ForeignKeyConstraint(['note_id'], ['notes.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_changes_note_id', 'changes', ['note_id'], unique=False)

def downgrade() -> None:
    """Restore the previous revision's tables (structure only)."""
    op.drop_table('changes')
    op.drop_table('notes')
    op.drop_table('trades')

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

    op.create_table(
        'usage_periods',
        sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ai_voice_limit', sa.Integer(), nullable=False),
        sa.Column('ai_voice_used', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            'ai_voice_used >= 0 AND ai_voice_used <= ai_voice_limit',
            name='ck_usage_periods_voice_counter',
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_usage_periods_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_usage_periods'),
        sa.UniqueConstraint('user_id', 'period_start', 'period_end', name='uq_usage_periods_user_period'),
    )
