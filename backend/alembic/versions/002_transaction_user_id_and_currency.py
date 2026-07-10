"""add user_id and currency to transactions, make account_id nullable

Revision ID: 002
Revises: 001
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add user_id column (not null with FK)
    op.add_column(
        "transactions", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key("fk_transactions_user_id", "transactions", "users", ["user_id"], ["id"])

    # Backfill user_id from account -> bank_connection -> user
    op.execute("""
        UPDATE transactions t
        SET user_id = bc.user_id
        FROM accounts a
        JOIN bank_connections bc ON a.connection_id = bc.id
        WHERE t.account_id = a.id AND t.user_id IS NULL
    """)

    # Now make it NOT NULL
    op.alter_column("transactions", "user_id", nullable=False)
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])

    # Add currency column
    op.add_column(
        "transactions", sa.Column("currency", sa.String(3), nullable=False, server_default="BRL")
    )

    # Make account_id nullable
    op.alter_column("transactions", "account_id", nullable=True)


def downgrade() -> None:
    op.alter_column("transactions", "account_id", nullable=False)
    op.drop_column("transactions", "currency")
    op.drop_index("ix_transactions_user_id", "transactions")
    op.drop_constraint("fk_transactions_user_id", "transactions", type_="foreignkey")
    op.drop_column("transactions", "user_id")
