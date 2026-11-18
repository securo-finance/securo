"""Track whether an agent's tool list has been written out, and let it hold
any server name.

An agent with no agent_tools rows used to be allowed every tool. Rows now
are the only grant, so agents that relied on "no rows" are marked as not
initialized: the next tool discovery for them records every tool it finds
as enabled, which keeps what they could already do. Agents that already
have rows keep exactly those.

Revision ID: 116
Revises: 115
"""

from alembic import op
import sqlalchemy as sa

revision = "116"
down_revision = "115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("tools_initialized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE agents SET tools_initialized = true "
        "WHERE EXISTS (SELECT 1 FROM agent_tools WHERE agent_tools.agent_id = agents.id)"
    )
    # Rows are now written for every discovered tool, and an extra MCP server
    # registered without an alias is named by its whole URL, which can pass
    # 80 characters. SQLite has no ALTER COLUMN TYPE and doesn't enforce a
    # VARCHAR length anyway.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "agent_tools", "server",
            existing_type=sa.String(80), type_=sa.String(500), existing_nullable=False,
        )


def downgrade() -> None:
    # agent_tools.server stays at 500: narrowing it would fail on a server
    # named by a long URL, and truncating would change where calls go.
    op.drop_column("agents", "tools_initialized")
