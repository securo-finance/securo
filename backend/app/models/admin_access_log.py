import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminAccessLog(Base):
    """One administrative action on an account or a workspace, with its reason (ADR 0042).

    The record outlives the people and the workspaces it names: the emails and
    the workspace name are snapshots, and the foreign keys clear
    (``ON DELETE SET NULL``) when the target goes away, so deleting it never
    deletes the evidence of the action.
    """

    __tablename__ = "admin_access_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: The administrator who acted.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[str] = mapped_column(String(255))
    #: The account acted on, when the target is an account.
    target_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The account's address when the action was taken; empty for a workspace.
    target_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: The workspace acted on, when the target is a workspace (ADR 0049).
    target_workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Its name when the action was taken, so a rename or a later deletion
    #: never rewrites what the record says.
    target_workspace_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    #: What was done: ``user.update``, ``user.delete`` or ``workspace.status``.
    action: Mapped[str] = mapped_column(String(40), index=True)
    #: Every field the action changed, as ``{field: {"from": ..., "to": ...}}``
    #: (#556). The email is the reason the record exists: a change of login
    #: address is what a takeover looked like before it was closed.
    changes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    #: Client address as the request saw it, at most 45 characters (IPv6 with a scope).
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class AdminAccessLogViewer(Base):
    """Who may read one administrative record (ADR 0042).

    Written with the record, from the target and the owners of the workspaces
    it belonged to at that moment. Reading it live instead would lose the
    audience the moment the target is deleted, because the deletion also takes
    the memberships that say who its owners were.
    """

    __tablename__ = "admin_access_log_viewer"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_access_log.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: No foreign key: the account may be gone by the time this is read.
    viewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, index=True)
