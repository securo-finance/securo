"""track provider-reported shared account balances

Revision ID: 075
Revises: 074
Create Date: 2026-08-24

Some Open Finance providers expose multiple physical credit cards against one
consolidated credit line. Keep an opaque provider-scoped grouping key so their
shared balance is counted once in aggregate views while the accounts remain
separate for bills and transactions.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("shared_balance_group", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "shared_balance_group")
