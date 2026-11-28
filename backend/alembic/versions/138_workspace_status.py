"""a workspace can be suspended, and changing it leaves a record (ADR 0052)

Revision ID: 138
Revises: 137
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "138"
down_revision = "137"
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

    # An administrative action may act on a workspace instead of an account,
    # and its owner reads it in the same log (ADR 0042, ADR 0052).
    op.alter_column(
        "admin_access_log",
        "target_email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.add_column(
        "admin_access_log",
        sa.Column("target_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "admin_access_log",
        sa.Column("target_workspace_name", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_admin_access_log_target_workspace_id",
        "admin_access_log",
        "workspaces",
        ["target_workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_admin_access_log_target_workspace_id",
        "admin_access_log",
        ["target_workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_access_log_target_workspace_id", table_name="admin_access_log")
    op.drop_constraint(
        "fk_admin_access_log_target_workspace_id", "admin_access_log", type_="foreignkey"
    )
    op.drop_column("admin_access_log", "target_workspace_name")
    op.drop_column("admin_access_log", "target_workspace_id")
    # The column is required again: a workspace entry has no address to keep.
    op.execute("UPDATE admin_access_log SET target_email = '' WHERE target_email IS NULL")
    op.alter_column(
        "admin_access_log",
        "target_email",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    op.drop_column("workspaces", "status_changed_at")
    op.drop_column("workspaces", "status")
