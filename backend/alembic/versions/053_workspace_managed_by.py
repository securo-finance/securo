"""add managed_by_user_id to workspaces

Revision ID: 053
Revises: 052
Create Date: 2026-05-26

Adds a nullable FK that expresses "this workspace is administered by
user X from outside its membership roster." The managed-by user has
effective owner rights in the workspace without being listed as a
member. Designed for setups where one user provisions and operates
workspaces on behalf of other people (e.g. external collaborators who
work across several distinct sets of books).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "managed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workspaces_managed_by_user_id",
        "workspaces",
        ["managed_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspaces_managed_by_user_id", table_name="workspaces")
    op.drop_column("workspaces", "managed_by_user_id")
