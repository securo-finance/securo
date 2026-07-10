"""agents foundation: agents, conversations, messages, knowledge, usage

Revision ID: 046
Revises: 045
Create Date: 2026-05-11

This migration is always applied; the agents feature itself is gated by
the AGENTS_ENABLED env var. Empty tables cost nothing.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

from app.agents.config import get_agent_settings

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


_PGVECTOR_MISSING_MSG = (
    "\n\n"
    "  pgvector is not installed in your Postgres server.\n"
    "  Securo v0.11.0+ requires pgvector for the agents knowledge base.\n\n"
    "  Fix: switch your `db` service image to a Postgres build that bundles\n"
    "  pgvector, then re-run the upgrade:\n\n"
    "      image: pgvector/pgvector:pg16   # was: postgres:16-alpine\n\n"
    "  Your data volume is preserved (same on-disk format).\n"
    "  See https://github.com/securo-finance/securo/releases/tag/v0.11.0\n"
)


def upgrade() -> None:
    # Pre-flight: refuse to start the migration unless pgvector is
    # physically available on the server. Without this guard the user
    # would see a 50-line asyncpg traceback for a one-line fix
    # (image swap), with the backend stuck in a crash loop in between.
    bind = op.get_bind()
    available = bind.execute(
        text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).first()
    if available is None:
        raise RuntimeError(_PGVECTOR_MISSING_MSG)

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    embed_dim = get_agent_settings().embedding_dim

    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False, server_default=""),
        sa.Column("icon", sa.String(50), nullable=False, server_default="bot"),
        sa.Column("color", sa.String(7), nullable=False, server_default="#6B7280"),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("temperature", sa.Float, nullable=False, server_default="0.4"),
        sa.Column("max_history_messages", sa.Integer, nullable=False, server_default="20"),
        sa.Column("top_n", sa.Integer, nullable=False, server_default="6"),
        sa.Column("similarity_threshold", sa.Float, nullable=False, server_default="0.25"),
        sa.Column("extra", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])

    op.create_table(
        "agent_tools",
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("server", sa.String(80), primary_key=True),
        sa.Column("tool_name", sa.String(120), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
    )

    op.create_table(
        "agent_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(40), nullable=False, server_default="web"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_conversations_agent_id", "agent_conversations", ["agent_id"])
    op.create_index("ix_agent_conversations_user_id", "agent_conversations", ["user_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("tool_calls", postgresql.JSON, nullable=True),
        sa.Column("tool_result", postgresql.JSON, nullable=True),
        sa.Column("citations", postgresql.JSON, nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_messages_conv_ord", "agent_messages", ["conversation_id", "ordinal"])

    op.create_table(
        "agent_knowledge_docs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", sa.String(500), nullable=True),
        sa.Column("mime", sa.String(80), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_knowledge_docs_agent_id", "agent_knowledge_docs", ["agent_id"])

    op.create_table(
        "agent_knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_knowledge_docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(embed_dim), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
    )
    op.create_index(
        "ix_agent_knowledge_chunks_doc_ord", "agent_knowledge_chunks", ["doc_id", "ordinal"]
    )
    # IVFFlat index for ANN search; tune lists later as corpus grows.
    op.execute(
        "CREATE INDEX ix_agent_knowledge_chunks_embedding "
        "ON agent_knowledge_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    op.create_table(
        "agent_llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="chat"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_llm_usage_user_id", "agent_llm_usage", ["user_id"])
    op.create_index("ix_agent_llm_usage_agent_id", "agent_llm_usage", ["agent_id"])


def downgrade() -> None:
    op.drop_table("agent_llm_usage")
    op.execute("DROP INDEX IF EXISTS ix_agent_knowledge_chunks_embedding")
    op.drop_table("agent_knowledge_chunks")
    op.drop_table("agent_knowledge_docs")
    op.drop_table("agent_messages")
    op.drop_table("agent_conversations")
    op.drop_table("agent_tools")
    op.drop_table("agents")
    # Leave the `vector` extension in place — other features may use it later.
