"""add user_id to accounts, make connection_id and external_id nullable

Revision ID: 003
Revises: 002
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add user_id column (nullable first for backfill)
    op.add_column("accounts", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_accounts_user_id", "accounts", "users", ["user_id"], ["id"])

    # Backfill user_id from bank_connection
    op.execute("""
        UPDATE accounts a
        SET user_id = bc.user_id
        FROM bank_connections bc
        WHERE a.connection_id = bc.id AND a.user_id IS NULL
    """)

    # Now make it NOT NULL
    op.alter_column("accounts", "user_id", nullable=False)
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    # Make connection_id nullable (manual accounts have no connection)
    op.alter_column("accounts", "connection_id", nullable=True)

    # Make external_id nullable (manual accounts have no external ID)
    op.alter_column("accounts", "external_id", nullable=True)


def downgrade() -> None:
    op.alter_column("accounts", "external_id", nullable=False)
    op.alter_column("accounts", "connection_id", nullable=False)
    op.drop_index("ix_accounts_user_id", "accounts")
    op.drop_constraint("fk_accounts_user_id", "accounts", type_="foreignkey")
    op.drop_column("accounts", "user_id")
