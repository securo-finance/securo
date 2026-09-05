import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.agents.models.conversation import Conversation
    from app.agents.models.knowledge import KnowledgeDoc


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(50), default="bot")
    color: Mapped[str] = mapped_column(String(7), default="#6B7280")

    # LLM config. There are three layers (most→least specific):
    #   1. agent.connection_id  — user-managed connection (recommended)
    #   2. agent.provider       — kind name; instance env vars supply creds
    #   3. instance defaults    — env vars only
    # `model` is the model id within whichever provider/connection wins.
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_llm_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.4)
    max_history_messages: Mapped[int] = mapped_column(Integer, default=20)

    # RAG tuning
    top_n: Mapped[int] = mapped_column(Integer, default=6)
    similarity_threshold: Mapped[float] = mapped_column(Float, default=0.25)

    # Free-form: per-agent overrides like {"language": "pt-BR"} or
    # {"channels": {"web": {...}, "whatsapp": {...}}}. Schema-light so we
    # can iterate without migrations.
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # When True, the executor prepends a small "context primer" system
    # message before each turn — user name, currency, today's date,
    # account list. Saves the user from having to repeat themselves.
    auto_context: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # At most one agent per user has is_default=True (enforced by partial
    # unique index). The global slide-over chat panel uses this agent.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tools: Mapped[list["AgentTool"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    knowledge_docs: Mapped[list["KnowledgeDoc"]] = relationship(back_populates="agent", cascade="all, delete-orphan")

    # Virtual attributes populated by agent_service queries (not DB columns).
    conversation_count = cast(int, 0)
    knowledge_count = cast(int, 0)


class AgentTool(Base):
    """Per-agent tool whitelist. Tools are discovered from registered MCP
    servers; this row records that the user has enabled a given tool for a
    given agent. Absence = disabled (closed-by-default for safety on writes).
    """
    __tablename__ = "agent_tools"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    server: Mapped[str] = mapped_column(String(80), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    agent: Mapped["Agent"] = relationship(back_populates="tools")
