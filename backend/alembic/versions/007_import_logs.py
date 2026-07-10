"""add import_logs table and import_id to transactions

Revision ID: 007
Revises: 006
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("total_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_import_logs_user_id", "import_logs", ["user_id"])

    op.add_column(
        "transactions",
        sa.Column(
            "import_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("import_logs.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "import_id")
    op.drop_index("ix_import_logs_user_id", "import_logs")
    op.drop_table("import_logs")
