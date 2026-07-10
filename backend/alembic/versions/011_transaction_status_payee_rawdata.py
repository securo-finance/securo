"""add status, payee, and raw_data columns to transactions

Revision ID: 011
Revises: 010
Create Date: 2026-03-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pending vs booked status (default "posted" for all existing transactions)
    op.add_column(
        "transactions",
        sa.Column("status", sa.String(10), nullable=False, server_default="posted"),
    )

    # Smart payee extracted from merchant/payment data
    op.add_column(
        "transactions",
        sa.Column("payee", sa.String(500), nullable=True),
    )

    # Full raw provider response for debugging and audit
    op.add_column(
        "transactions",
        sa.Column("raw_data", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "raw_data")
    op.drop_column("transactions", "payee")
    op.drop_column("transactions", "status")
