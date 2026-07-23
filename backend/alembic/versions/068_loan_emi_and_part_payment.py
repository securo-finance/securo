"""add loan monthly_emi field

Revision ID: 068
Revises: 067
Create Date: 2026-07-23

Add monthly_emi column to accounts table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("monthly_emi", sa.Numeric(precision=15, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "monthly_emi")
