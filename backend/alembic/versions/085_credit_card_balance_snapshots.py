"""preserve credit card balance snapshots

Revision ID: 085
Revises: 084
Create Date: 2026-08-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("expected_balance", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("available_credit", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    # Enable Banking cash-account signs are the inverse of Securo's card
    # invariant. Existing rows were stored verbatim, so normalize them once;
    # later syncs perform this conversion at the provider boundary.
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET balance = -accounts.balance
            FROM bank_connections
            WHERE accounts.connection_id = bank_connections.id
              AND accounts.type = 'credit_card'
              AND bank_connections.provider = 'enable_banking'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE accounts
            SET balance = -accounts.balance
            FROM bank_connections
            WHERE accounts.connection_id = bank_connections.id
              AND accounts.type = 'credit_card'
              AND bank_connections.provider = 'enable_banking'
            """
        )
    )
    op.drop_column("accounts", "available_credit")
    op.drop_column("accounts", "expected_balance")
