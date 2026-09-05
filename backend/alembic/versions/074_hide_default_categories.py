"""add hidden flags to categories and category groups

Revision ID: 074
Revises: 073
Create Date: 2026-07-30

Users cannot delete seeded system categories/groups because historical
transactions may still point at them. Add an explicit visibility flag so the
UI can hide defaults without destructive data changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "category_groups",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("category_groups", "is_hidden")
    op.drop_column("categories", "is_hidden")
