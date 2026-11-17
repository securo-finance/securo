"""Track whether an agent's tool list has been written out.

An agent with no agent_tools rows used to be allowed every tool. Rows now
are the only grant, so agents that relied on "no rows" are marked as not
initialized: the next tool discovery for them records every tool it finds
as enabled, which keeps what they could already do. Agents that already
have rows keep exactly those.

Revision ID: 114
Revises: 113
"""

from alembic import op
import sqlalchemy as sa

revision = "114"
down_revision = "113"
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


def downgrade() -> None:
    op.drop_column("agents", "tools_initialized")
