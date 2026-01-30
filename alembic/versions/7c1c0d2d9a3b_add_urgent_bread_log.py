"""add-urgent-bread-log

Revision ID: 7c1c0d2d9a3b
Revises: 2b28a76ece5a
Create Date: 2026-01-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7c1c0d2d9a3b'
down_revision: Union[str, None] = '2b28a76ece5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'urgent_bread_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('urgent_id', sa.String(length=64), nullable=False),
        sa.Column('bakery_id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('original_breads_json', sa.Unicode(), nullable=False),
        sa.Column('remaining_breads_json', sa.Unicode(), nullable=False),
        sa.Column('register_date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('done_date', sa.DateTime(), nullable=True),
        sa.Column('cancel_date', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bakery_id'], ['bakery.bakery_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('urgent_id'),
    )
    op.create_index(op.f('ix_urgent_bread_log_urgent_id'), 'urgent_bread_log', ['urgent_id'], unique=True)
    op.create_index('ix_urgent_bread_log_bakery_id', 'urgent_bread_log', ['bakery_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_urgent_bread_log_bakery_id', table_name='urgent_bread_log')
    op.drop_index(op.f('ix_urgent_bread_log_urgent_id'), table_name='urgent_bread_log')
    op.drop_table('urgent_bread_log')
