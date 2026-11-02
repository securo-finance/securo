"""add personal access tokens

Revision ID: 102
Revises: 101
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_personal_access_tokens_token_hash", "personal_access_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_personal_access_tokens_user_id", "personal_access_tokens", ["user_id"])
    op.create_index(
        "ix_personal_access_tokens_workspace_id", "personal_access_tokens", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("personal_access_tokens")
