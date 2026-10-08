"""add_is_income_to_category

Revision ID: a9338a1741a6
Revises: 096
Create Date: 2026-10-08 16:15:32.365872

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9338a1741a6'
down_revision: Union[str, Sequence[str], None] = '096'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add is_income column to categories table
    op.add_column('categories', sa.Column('is_income', sa.Boolean(), nullable=True, server_default='false'))

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('categories', 'is_income')
