"""Let an agent's tool list hold any MCP tool name.

Tool lists are now written for every discovered tool, and the MCP
specification lets a server name a tool with up to 128 characters, past the
120 agent_tools.tool_name held. Recording such a tool failed and ended the
chat turn. The column is widened, never truncated: the name routes the call.

Revision ID: 117
Revises: 116
"""

from alembic import op
import sqlalchemy as sa

revision = "117"
down_revision = "116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite has no ALTER COLUMN TYPE and doesn't enforce a VARCHAR length.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "agent_tools", "tool_name",
            existing_type=sa.String(120), type_=sa.String(255), existing_nullable=False,
        )


def downgrade() -> None:
    # Kept at 255: narrowing would fail on a long tool name already recorded.
    pass
