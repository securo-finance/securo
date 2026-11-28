"""assistant messages per month: the fifth usage limit

A fifth nullable counter on `user_quotas`, and on each agent message whether
the instance's own LLM connection answered it, which is the fact the monthly
count reads (#1968, ADR 0053).

Revision ID: 141
Revises: 140
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "141"
down_revision: Union[str, None] = "140"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_quotas", sa.Column("max_assistant_messages_per_month", sa.Integer(), nullable=True))
    op.add_column(
        "agent_messages",
        sa.Column("instance_llm", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("agent_messages", "instance_llm")
    op.drop_column("user_quotas", "max_assistant_messages_per_month")
