import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UiLayout(Base):
    """How one person arranged a surface (the dashboard, the reports) in one workspace.

    `items` is the ordered list of what they chose from the library, as
    `{"id": ...}` entries. No row means Securo's default arrangement; ids the
    frontend no longer knows are skipped when it renders, never an error.
    """

    __tablename__ = "ui_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "surface", name="uq_ui_layouts_user_workspace_surface"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    surface: Mapped[str] = mapped_column(String(32))
    items: Mapped[list[dict]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
