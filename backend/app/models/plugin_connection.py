import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PluginConnection(Base):
    """One connection a workspace made through a plugin's integration.

    What the person typed that is secret, and whatever the plugin got back from
    signing in, is sealed in `secrets` (see `app.plugins.vault`) and only ever
    sent to that plugin's process. `config` holds the non-secret fields and
    `state` the plugin's own cursor between syncs. When the plugin declared
    `api_scopes`, `access_token_id` is the scoped token it acts with; removing
    the connection revokes it.

    An integration that `provides` records syncs them into its own bank
    connection, `bank_connection_id`, created along with this one.
    """

    __tablename__ = "plugin_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    plugin_id: Mapped[str] = mapped_column(String(120))
    integration: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    # pending: waiting for its first sync; needs_auth: sign-in to finish or redo.
    status: Mapped[str] = mapped_column(String(20), default="pending")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secrets: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    sync_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    access_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("access_tokens.id", ondelete="SET NULL"), nullable=True
    )
    bank_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_connections.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PluginConnectionRoutine(Base):
    """One routine of a connection's integration, as it last ran (ADR 0035).

    A row per routine the integration declares, kept in step with the
    installed manifest (`connections.follow_manifest`). `every_minutes` and
    `timeout_seconds` are copied from it, as `sync_minutes` is for the
    connection, so the schedule reads them without the manifest. `state` is
    the routine's own, apart from the connection's sync cursor.
    """

    __tablename__ = "plugin_connection_routines"
    __table_args__ = (UniqueConstraint("connection_id", "routine", name="uq_plugin_connection_routine"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plugin_connections.id", ondelete="CASCADE"), index=True
    )
    routine: Mapped[str] = mapped_column(String(64))
    every_minutes: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
