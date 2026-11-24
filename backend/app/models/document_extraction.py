import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentExtraction(Base):
    """One document someone asked Securo to read (ADR 0037).

    The file sits in storage under the workspace's prefix; `result` is what
    the model read, kept as it came so a retry or a later look never needs
    the model again. Nothing here is a transaction: rows land only when a
    person confirms them, through the import (`import_log_id`) or on a
    single transaction (`transaction_id`). `confirmed` keeps the rows as the
    person accepted them, next to `result`, so what they corrected is known.
    """

    __tablename__ = "document_extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # statement: many rows into one account; receipt: one purchase.
    kind: Mapped[str] = mapped_column(String(20))
    # pending, processing, done, failed
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # text: the PDF's own text was read; vision: its pages or the photo.
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    pages: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # A short code the UI translates (vision_required, not_configured, ...)
    # and the provider's own message for the details.
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Same issuer and layout give the same value: what the next document of
    # that format can learn from (the account it went to, for now).
    layout_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    import_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_logs.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    confirmed: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    confirmed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceDocumentAiSettings(Base):
    """Which LLM connection reads this workspace's documents.

    The connection is one of the agents module's (`agent_llm_connections`),
    picked by a workspace owner from their own: provider, URL and key live
    there and nowhere else. No row, or no connection, means reading is off
    for the workspace. `model` replaces the connection's default model, and
    `vision` corrects the detection of whether that model reads images.
    """

    __tablename__ = "workspace_document_ai_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    # No foreign key on purpose: the agents models are imported only while
    # that module is on, and a key here would tie the core schema to them. A
    # connection deleted since reads as no connection: reading is off.
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # auto, true or false
    vision: Mapped[str] = mapped_column(String(10), server_default="auto")
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
