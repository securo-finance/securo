"""add collections (account groups for reporting/filtering)

Revision ID: 056
Revises: 055
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("icon", sa.String(length=50), nullable=False, server_default="folder"),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#6366F1"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collections_workspace_id", "collections", ["workspace_id"])

    op.create_table(
        "collection_accounts",
        sa.Column("collection_id", UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collection_id", "account_id"),
    )


def downgrade() -> None:
    op.drop_table("collection_accounts")
    op.drop_index("ix_collections_workspace_id", table_name="collections")
    op.drop_table("collections")
