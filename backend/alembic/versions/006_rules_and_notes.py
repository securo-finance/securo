# backend/alembic/versions/006_rules_and_notes.py
"""add rules table and notes to transactions

Revision ID: 006
Revises: 005
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add notes column to transactions
    op.add_column("transactions", sa.Column("notes", sa.Text(), nullable=True))

    # 2. Create new rules table
    op.create_table(
        "rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("conditions_op", sa.String(3), nullable=False, server_default="and"),
        sa.Column("conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_rules_user_id", "rules", ["user_id"])

    # 3. Drop old categorization_rules table
    op.drop_table("categorization_rules")


def downgrade() -> None:
    op.create_table(
        "categorization_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("pattern", sa.String(255), nullable=False),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    op.drop_index("ix_rules_user_id", "rules")
    op.drop_table("rules")
    op.drop_column("transactions", "notes")
