"""modules an account's workspaces may use on a managed instance

A neutral list of module ids per user, written by the operator, and its copy
on each workspace the user created, read only when MANAGED_INSTANCE is on
(ADR 0054). No row, and a null copy, mean every module.

Revision ID: 137
Revises: 136
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "137"
down_revision: Union[str, None] = "136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_module_limits",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("modules", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("workspaces", sa.Column("module_allowlist", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "module_allowlist")
    op.drop_table("user_module_limits")
