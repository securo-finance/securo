"""per-account usage limits for a managed instance

Four neutral counters per user, written by the operator and read only when
MANAGED_INSTANCE is on (ADR 0049). A null counter is unbounded.

Revision ID: 136
Revises: 135
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "136"
down_revision: Union[str, None] = "135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_quotas",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("max_personal_workspaces", sa.Integer(), nullable=True),
        sa.Column("max_business_workspaces", sa.Integer(), nullable=True),
        sa.Column("max_bank_connections", sa.Integer(), nullable=True),
        sa.Column("max_members_per_workspace", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_quotas")
