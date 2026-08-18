import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class LlmConnection(Base):
    """User-managed LLM endpoint config.

    One row per (user, named connection). Agents reference these via
    `agents.connection_id`. When an agent has no connection set, the
    executor falls back to the instance-wide env-var defaults so the
    feature still works out of the box.
    """
    __tablename__ = "agent_llm_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(120))
    # kind ∈ {"ollama","openai","anthropic","openai_compatible","orcarouter"}
    kind: Mapped[str] = mapped_column(String(40))

    # Endpoint base URL. Optional for openai/anthropic (they have defaults).
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Fernet ciphertext (base64). Decrypted on use only — see crypto.py.
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Just a hint shown in the agent form when picking this connection.
    default_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Free-form per-connection config (org_id, project, custom headers, etc.)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    # When an agent doesn't pick a connection explicitly, the user's
    # is_default=True row wins over the env-based fallback.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
