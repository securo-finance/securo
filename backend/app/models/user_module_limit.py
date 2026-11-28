import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserModuleLimit(Base):
    """The modules an account's workspaces may use on a managed instance (ADR 0054).

    Neutral, like the quotas of ADR 0049: the operator writes a list of module
    ids and the code attaches nothing else to it. A user with no row may use
    every module. The list is also copied onto each workspace the user created
    (`Workspace.module_allowlist`), so resolving a workspace's modules reads no
    other table.
    """

    __tablename__ = "user_module_limits"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    modules: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
