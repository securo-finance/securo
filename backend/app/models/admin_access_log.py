import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminAccessLog(Base):
    """One administrative action on a person's account, with its reason (ADR 0042).

    The record outlives the people it names: the emails are snapshots, and the
    foreign keys clear (``ON DELETE SET NULL``) when an account goes away, so
    deleting the target never deletes the evidence of the deletion.
    """

    __tablename__ = "admin_access_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: The administrator who acted.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[str] = mapped_column(String(255))
    #: The account acted on.
    target_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_email: Mapped[str] = mapped_column(String(255))
    #: What was done: ``user.update`` or ``user.delete``.
    action: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(Text)
    #: Client address as the request saw it, at most 45 characters (IPv6 with a scope).
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
