"""data plugins: origin on categories and groups, plugins installed per workspace

Categories and category groups get the same origin columns rules got in 107
(source, source_key, source_hash), so a data plugin can update and remove
what it created without touching what the user changed. Existing rows are
left with no origin: they are the user's or Securo's defaults, never a
plugin's.

workspace_plugins records which catalog plugin is installed in a workspace
and at which commit.

Revision ID: 108
Revises: 107
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None

_ORIGIN_TABLES = ("categories", "category_groups")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in _ORIGIN_TABLES:
        if "source" not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("source", sa.String(120), nullable=True))
            op.add_column(table, sa.Column("source_key", sa.String(64), nullable=True))
            op.add_column(table, sa.Column("source_hash", sa.String(64), nullable=True))
            op.create_index(f"ix_{table}_workspace_source", table, ["workspace_id", "source"])

    if "workspace_plugins" not in inspector.get_table_names():
        op.create_table(
            "workspace_plugins",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("plugin_id", sa.String(120), nullable=False),
            sa.Column("name", sa.JSON(), nullable=False),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("commit", sa.String(40), nullable=False),
            sa.Column(
                "installed_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("workspace_id", "plugin_id", name="uq_workspace_plugins_workspace_plugin"),
        )
        op.create_index("ix_workspace_plugins_workspace_id", "workspace_plugins", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_plugins_workspace_id", table_name="workspace_plugins")
    op.drop_table("workspace_plugins")
    for table in _ORIGIN_TABLES:
        op.drop_index(f"ix_{table}_workspace_source", table_name=table)
        op.drop_column(table, "source_hash")
        op.drop_column(table, "source_key")
        op.drop_column(table, "source")
