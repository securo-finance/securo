"""admin access log: every administrative action on an account, with its reason

Revision ID: 133
Revises: 132

Letting an administrator change a person's account is only defensible if the
person can see it happened and why (ADR 0042). The emails are snapshots; the
foreign keys clear when an account is deleted, so the record outlives it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "133"
down_revision = "132"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An address an administrator asks to put on an account, until the person
    # whose account it is accepts it (#556). The account keeps its email and
    # its recovery until then, so asking is never a way in.
    op.add_column("users", sa.Column("pending_email", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("pending_email_requested_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "admin_access_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(255), nullable=False),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_email", sa.String(255), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        # Every field the action changed, old value and new (#556).
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_access_log_actor_id", "admin_access_log", ["actor_id"])
    op.create_index("ix_admin_access_log_target_user_id", "admin_access_log", ["target_user_id"])
    op.create_index("ix_admin_access_log_action", "admin_access_log", ["action"])
    op.create_index("ix_admin_access_log_created_at", "admin_access_log", ["created_at"])

    # Who may read each record, kept apart from the account and its
    # memberships so the audience survives both (ADR 0042).
    op.create_table(
        "admin_access_log_viewer",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_access_log.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("viewer_id", postgresql.UUID(as_uuid=True), primary_key=True),
    )
    op.create_index(
        "ix_admin_access_log_viewer_viewer_id", "admin_access_log_viewer", ["viewer_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_access_log_viewer_viewer_id", table_name="admin_access_log_viewer")
    op.drop_table("admin_access_log_viewer")
    op.drop_index("ix_admin_access_log_created_at", table_name="admin_access_log")
    op.drop_index("ix_admin_access_log_action", table_name="admin_access_log")
    op.drop_index("ix_admin_access_log_target_user_id", table_name="admin_access_log")
    op.drop_index("ix_admin_access_log_actor_id", table_name="admin_access_log")
    op.drop_table("admin_access_log")

    op.drop_column("users", "pending_email_requested_at")
    op.drop_column("users", "pending_email")
