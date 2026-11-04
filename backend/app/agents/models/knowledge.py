import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.agents.config import get_agent_settings
from app.core.database import Base

if TYPE_CHECKING:
    from app.agents.models.agent import Agent

_EMBED_DIM = get_agent_settings().embedding_dim


class KnowledgeDoc(Base):
    __tablename__ = "agent_knowledge_docs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))

    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    mime: Mapped[str] = mapped_column(String(80))
    storage_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    # status ∈ {"pending","processing","ready","failed"}
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    agent: Mapped["Agent"] = relationship(back_populates="knowledge_docs")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="doc", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "agent_knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_knowledge_docs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True)

    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(_EMBED_DIM), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    doc: Mapped["KnowledgeDoc"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_agent_knowledge_chunks_doc_ord", "doc_id", "ordinal"),
    )
