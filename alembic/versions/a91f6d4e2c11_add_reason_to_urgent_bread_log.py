"""add reason to urgent_bread_log

Revision ID: a91f6d4e2c11
Revises: 7c1c0d2d9a3b
Create Date: 2026-02-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a91f6d4e2c11'
down_revision: Union[str, None] = '7c1c0d2d9a3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'urgent_bread_log',
        sa.Column('reason', sa.Unicode(), nullable=False, server_default='')
    )


def downgrade() -> None:
    op.drop_column('urgent_bread_log', 'reason')
