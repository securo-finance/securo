import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkspacePlugin(Base):
    """A data plugin from the catalog installed in a workspace, and at which commit.

    The items it created carry their own origin (`PluginOrigin`); this row is
    what tells an update apart and what the catalog shows as installed.
    Built-in rule packs ship with Securo and have no row here.
    """

    __tablename__ = "workspace_plugins"
    __table_args__ = (UniqueConstraint("workspace_id", "plugin_id", name="uq_workspace_plugins_workspace_plugin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    plugin_id: Mapped[str] = mapped_column(String(120))
    # The plugin's name per locale, kept so the installed list needs no catalog.
    name: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(32))
    commit: Mapped[str] = mapped_column(String(40))
    installed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
