"""add virtual pocket allocations to goals

Revision ID: 097
Revises: 096
Create Date: 2026-09-19

Allocations are metadata over existing account money. They deliberately live
outside transactions so reserving money never creates a second financial
movement or changes net-worth, budget, and cash-flow totals.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "097"
down_revision: Union[str, None] = "096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "goal_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "goal_id", "transaction_id", name="uq_goal_allocations_goal_transaction"
        ),
        sa.CheckConstraint("amount <> 0", name="ck_goal_allocations_amount_nonzero"),
        sa.CheckConstraint(
            "source IN ('opening', 'adjustment', 'transaction')",
            name="ck_goal_allocations_source",
        ),
        sa.CheckConstraint(
            "(source = 'transaction' AND transaction_id IS NOT NULL) OR "
            "(source IN ('opening', 'adjustment') AND transaction_id IS NULL)",
            name="ck_goal_allocations_source_transaction",
        ),
    )
    op.create_index("ix_goal_allocations_workspace_id", "goal_allocations", ["workspace_id"])
    op.create_index("ix_goal_allocations_goal_id", "goal_allocations", ["goal_id"])
    op.create_index("ix_goal_allocations_transaction_id", "goal_allocations", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("goal_allocations")
