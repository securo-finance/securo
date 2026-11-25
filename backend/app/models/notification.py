import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Notification(Base):
    """Something a person should know about a workspace, while it holds (ADR 0041).

    One row per person: reading is personal. `data` carries ids, names and
    amounts as strings, never a sentence: the client writes the text, in its
    language and respecting privacy mode. `resolved_at` is set once what the
    row says stops being true; the row stays, so a condition that comes back
    soon does not ask for attention again.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "dedup_key", name="uq_notifications_workspace_user_key"),
        Index("ix_notifications_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # A `NotificationKind` (`app/schemas/notification.py`).
    kind: Mapped[str] = mapped_column(String(32))
    dedup_key: Mapped[str] = mapped_column(String(160))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # When it last asked for attention: created, or reopened after a while.
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPreference(Base):
    """Whether someone wants a kind of notification on a channel, in one workspace.

    Nothing stored means the channel's own default (`notification_channels`):
    on inside the app, off anywhere else. Rows of a kind turned off inside the
    app are still kept up to date, only never shown: turning it back on shows
    what holds at once.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "kind", "channel", name="uq_notification_preferences_user_kind_channel"
        ),
        Index("ix_notification_preferences_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean)


class NotificationDelivery(Base):
    """A notification on its way out through a channel outside the app.

    Written with the notification, in the same transaction, and sent later by
    `notification_tasks.deliver_notifications`. One per time it asked for
    attention: `notified_at` says which, so a reminder is sent again and a
    retry is not.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel", "notified_at", name="uq_notification_deliveries_notification_channel_time"
        ),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(32))
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # pending, sent, skipped (nothing left to say) or failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A short code, never the service's own words: those can carry an address.
    error: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
