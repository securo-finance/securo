"""mcp issued tokens + denylist for revocable external JWTs

Revision ID: 079
Revises: 078
Create Date: 2026-09-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_issued_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=False, server_default="external"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_issued_tokens_jti", "mcp_issued_tokens", ["jti"], unique=True)
    op.create_index("ix_mcp_issued_tokens_token_hash", "mcp_issued_tokens", ["token_hash"], unique=True)
    op.create_index("ix_mcp_issued_tokens_user_id", "mcp_issued_tokens", ["user_id"])
    op.create_index("ix_mcp_issued_tokens_workspace_id", "mcp_issued_tokens", ["workspace_id"])

    op.create_table(
        "mcp_token_denylist",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("jti", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_token_denylist_jti", "mcp_token_denylist", ["jti"])


def downgrade() -> None:
    op.drop_index("ix_mcp_token_denylist_jti", table_name="mcp_token_denylist")
    op.drop_table("mcp_token_denylist")
    op.drop_index("ix_mcp_issued_tokens_workspace_id", table_name="mcp_issued_tokens")
    op.drop_index("ix_mcp_issued_tokens_user_id", table_name="mcp_issued_tokens")
    op.drop_index("ix_mcp_issued_tokens_token_hash", table_name="mcp_issued_tokens")
    op.drop_index("ix_mcp_issued_tokens_jti", table_name="mcp_issued_tokens")
    op.drop_table("mcp_issued_tokens")
