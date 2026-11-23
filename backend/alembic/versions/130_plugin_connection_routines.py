"""plugin connection routines: scheduled work apart from a connection's sync

Revision ID: 130
Revises: 129

An integration can declare routines that each run on a schedule of their own
(ADR 0035). A row per connection and routine keeps when it last ran, its
failures and its own state.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "130"
down_revision = "129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_connection_routines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plugin_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("routine", sa.String(64), nullable=False),
        sa.Column("every_minutes", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("connection_id", "routine", name="uq_plugin_connection_routine"),
    )
    op.create_index(
        "ix_plugin_connection_routines_connection_id", "plugin_connection_routines", ["connection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_connection_routines_connection_id", table_name="plugin_connection_routines")
    op.drop_table("plugin_connection_routines")
