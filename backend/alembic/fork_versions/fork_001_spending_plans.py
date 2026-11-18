"""add daily spending plans

Revision ID: fork_001
Revises: 113
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "fork_001"
down_revision = "113"
branch_labels = ("fork",)
depends_on = None


def upgrade() -> None:
    if "spending_plans" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "spending_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("daily_allowance", sa.Numeric(15, 2), nullable=False),
        sa.Column("balance_floor", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("account_ids", sa.JSON(), nullable=True),
        sa.Column("horizon_months", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_spending_plans_workspace_id", "spending_plans", ["workspace_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_spending_plans_workspace_id", table_name="spending_plans")
    op.drop_table("spending_plans")
