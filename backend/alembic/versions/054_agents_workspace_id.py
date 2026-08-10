"""add workspace_id to agents + conversations

Revision ID: 054
Revises: 053
Create Date: 2026-05-26

Agents are tenant-scoped: a workspace's own assistants don't show up
in another workspace. Conversations inherit the workspace from the
agent they were started against — switching workspaces during a chat
isn't supported (the agent itself doesn't follow).

Backfill: every existing agent + conversation reparents to its owning
user's first (oldest) workspace. After backfill the column is NOT NULL
and indexed.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("agents", "agent_conversations"):
        op.add_column(
            table,
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        # Backfill via the user's first workspace.
        op.execute(
            f"""
            UPDATE {table} t
            SET workspace_id = w.id
            FROM workspaces w
            WHERE w.created_by_user_id = t.user_id
              AND w.kind = 'personal'
            """
        )
        op.alter_column(table, "workspace_id", nullable=False)
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])


def downgrade() -> None:
    for table in ("agents", "agent_conversations"):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_column(table, "workspace_id")
