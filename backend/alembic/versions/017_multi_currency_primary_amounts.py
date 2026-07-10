"""add multi-currency primary amount columns

Revision ID: 017
Revises: 016
Create Date: 2026-03-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Transactions
    op.add_column(
        "transactions",
        sa.Column("amount_primary", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.add_column(
        "transactions", sa.Column("fx_rate_used", sa.Numeric(precision=20, scale=10), nullable=True)
    )

    # Recurring transactions
    op.add_column(
        "recurring_transactions",
        sa.Column("amount_primary", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("fx_rate_used", sa.Numeric(precision=20, scale=10), nullable=True),
    )

    # Accounts
    op.add_column(
        "accounts", sa.Column("balance_primary", sa.Numeric(precision=15, scale=2), nullable=True)
    )

    # Assets
    op.add_column(
        "assets",
        sa.Column("purchase_price_primary", sa.Numeric(precision=15, scale=2), nullable=True),
    )

    # Budgets
    op.add_column(
        "budgets", sa.Column("currency", sa.String(3), server_default="BRL", nullable=True)
    )
    op.add_column(
        "budgets", sa.Column("amount_primary", sa.Numeric(precision=15, scale=2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("budgets", "amount_primary")
    op.drop_column("budgets", "currency")
    op.drop_column("assets", "purchase_price_primary")
    op.drop_column("accounts", "balance_primary")
    op.drop_column("recurring_transactions", "fx_rate_used")
    op.drop_column("recurring_transactions", "amount_primary")
    op.drop_column("transactions", "fx_rate_used")
    op.drop_column("transactions", "amount_primary")
