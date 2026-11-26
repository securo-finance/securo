"""a workspace can be suspended (ADR 0049)

Revision ID: 136
Revises: 135
"""

import sqlalchemy as sa
from alembic import op

revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column(
        "workspaces",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "status_changed_at")
    op.drop_column("workspaces", "status")
