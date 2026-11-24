"""document extractions: documents read into transactions, and the connection each workspace reads with

Revision ID: 131
Revises: 130

A statement PDF or a receipt photo read by an LLM (ADR 0037). The table keeps
the file's place in storage, what the model read and what the person
confirmed; the second one holds which agents LLM connection a workspace
reads with.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "131"
down_revision = "130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("pages", sa.BigInteger(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("layout_fingerprint", sa.String(64), nullable=True),
        sa.Column(
            "import_log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("import_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed", sa.JSON(), nullable=True),
        sa.Column(
            "confirmed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_document_extractions_workspace_id", "document_extractions", ["workspace_id"])
    op.create_index(
        "ix_document_extractions_layout_fingerprint", "document_extractions", ["layout_fingerprint"]
    )

    op.create_table(
        "workspace_document_ai_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("vision", sa.String(10), nullable=False, server_default="auto"),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_workspace_document_ai_settings_workspace_id",
        "workspace_document_ai_settings",
        ["workspace_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_document_ai_settings_workspace_id", table_name="workspace_document_ai_settings")
    op.drop_table("workspace_document_ai_settings")
    op.drop_index("ix_document_extractions_layout_fingerprint", table_name="document_extractions")
    op.drop_index("ix_document_extractions_workspace_id", table_name="document_extractions")
    op.drop_table("document_extractions")
