"""add note to customer

Revision ID: c3d7e9a4b1f2
Revises: a91f6d4e2c11
Create Date: 2026-02-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d7e9a4b1f2'
down_revision: Union[str, None] = 'a91f6d4e2c11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'customer',
        sa.Column('note', sa.Unicode(), nullable=False, server_default='')
    )


def downgrade() -> None:
    op.drop_column('customer', 'note')
