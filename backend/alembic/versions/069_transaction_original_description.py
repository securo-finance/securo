"""add original transaction description

Revision ID: 069
Revises: 068
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("original_description", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "original_description")
