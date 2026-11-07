import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (Index("ix_rules_workspace_source", "workspace_id", "source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    conditions_op: Mapped[str] = mapped_column(String(3), default="and")  # "and" | "or"
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set when the rule came from a pack: the pack's id, the rule's key in
    # it, and a hash of the rule as installed. A rule whose content no
    # longer matches the hash was edited and belongs to the user.
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship()
