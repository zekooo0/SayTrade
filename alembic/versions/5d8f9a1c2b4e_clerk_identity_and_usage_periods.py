"""clerk identity + usage periods

Adds users.clerk_sub (Clerk's external subject id — Clerk IDs are not UUIDs,
so the internal uuid PK stays the FK target and the mapping lives in this
column) and the billing-module usage_periods table with the monetization
spec's fields and its atomic-reservation invariant.

Revision ID: 5d8f9a1c2b4e
Revises: 3e626f4329ed
Create Date: 2026-09-15 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5d8f9a1c2b4e'
down_revision: Union[str, Sequence[str], None] = '3e626f4329ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the Clerk mapping column and the entitlement counter table."""
    # users is empty (verified before the rebuild), so a NOT NULL column
    # without a default is safe here.
    op.add_column(
        'users',
        sa.Column('clerk_sub', sa.String(length=64), nullable=False),
    )
    op.create_unique_constraint('uq_users_clerk_sub', 'users', ['clerk_sub'])

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
    op.create_index('ix_usage_periods_user_id', 'usage_periods', ['user_id'], unique=False)


def downgrade() -> None:
    """Drop the entitlement counter and the Clerk mapping column."""
    op.drop_table('usage_periods')
    op.drop_constraint('uq_users_clerk_sub', 'users', type_='unique')
    op.drop_column('users', 'clerk_sub')