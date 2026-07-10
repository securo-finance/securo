"""add payees and payee_mapping tables, add payee_id to transactions

Revision ID: 019
Revises: 018
Create Date: 2026-04-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create payees table
    op.create_table(
        "payees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), server_default="merchant", nullable=False),
        sa.Column("is_favorite", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_payees_user_id", "payees", ["user_id"])
    op.create_unique_constraint("uq_payees_user_id_name", "payees", ["user_id", "name"])

    # Create payee_mapping table (for merge/dedup)
    op.create_table(
        "payee_mapping",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "target_id",
            UUID(as_uuid=True),
            sa.ForeignKey("payees.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_payee_mapping_user_id_target_id", "payee_mapping", ["user_id", "target_id"])

    # Add payee_id FK to transactions
    op.add_column(
        "transactions",
        sa.Column(
            "payee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("payees.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_transactions_payee_id", "transactions", ["payee_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_payee_id", table_name="transactions")
    op.drop_column("transactions", "payee_id")
    op.drop_index("ix_payee_mapping_user_id_target_id", table_name="payee_mapping")
    op.drop_table("payee_mapping")
    op.drop_constraint("uq_payees_user_id_name", "payees", type_="unique")
    op.drop_index("ix_payees_user_id", table_name="payees")
    op.drop_table("payees")
