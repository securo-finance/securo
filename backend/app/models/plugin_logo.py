import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PluginLogo(Base):
    """A logo a plugin ships in its package, kept per installed commit.

    Extracted from the package on install (see `app.plugins.logos`) and served
    by Securo at `/api/plugins/logos/<sha256>`, so an account's institution
    logo never makes the browser reach a host the plugin didn't declare.
    """

    __tablename__ = "plugin_logos"
    __table_args__ = (UniqueConstraint("plugin_id", "commit", "key", name="uq_plugin_logos_plugin_commit_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plugin_id: Mapped[str] = mapped_column(String(120))
    commit: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
