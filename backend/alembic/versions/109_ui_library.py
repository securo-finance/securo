"""ui library: a chosen theme per user, an arranged dashboard and reports per workspace

users.theme keeps the appearance someone picked from the library; null is
Securo's own look. ui_layouts keeps, per user and workspace, which widgets
the dashboard shows and which reports are listed, in order. No row means the
default arrangement, so nothing changes for anyone until they customize.

Revision ID: 109
Revises: 108
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "theme" not in {c["name"] for c in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("theme", sa.String(160), nullable=True))

    if "ui_layouts" not in inspector.get_table_names():
        op.create_table(
            "ui_layouts",
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
            sa.Column("surface", sa.String(32), nullable=False),
            sa.Column("items", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "workspace_id", "surface", name="uq_ui_layouts_user_workspace_surface"),
        )
        op.create_index("ix_ui_layouts_workspace_id", "ui_layouts", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_ui_layouts_workspace_id", table_name="ui_layouts")
    op.drop_table("ui_layouts")
    op.drop_column("users", "theme")
