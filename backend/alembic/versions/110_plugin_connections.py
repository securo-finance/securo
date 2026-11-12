"""plugin connections: what a workspace connected through a plugin with code

workspace_plugins.manifest keeps the manifest a plugin with code was
installed with, so its integrations can be set up without the catalog.
plugin_connections holds each connection: non-secret settings, the sealed
secrets, the plugin's sync cursor and the scoped token it acts with.

Revision ID: 110
Revises: 109
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "110"
down_revision = "109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "manifest" not in {c["name"] for c in inspector.get_columns("workspace_plugins")}:
        op.add_column("workspace_plugins", sa.Column("manifest", sa.JSON(), nullable=True))

    if "plugin_connections" not in inspector.get_table_names():
        op.create_table(
            "plugin_connections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("plugin_id", sa.String(120), nullable=False),
            sa.Column("integration", sa.String(64), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("secrets", sa.Text(), nullable=True),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("sync_minutes", sa.Integer(), nullable=True),
            sa.Column(
                "access_token_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("access_tokens.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_plugin_connections_workspace_id", "plugin_connections", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_plugin_connections_workspace_id", table_name="plugin_connections")
    op.drop_table("plugin_connections")
    op.drop_column("workspace_plugins", "manifest")
