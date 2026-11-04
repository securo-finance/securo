import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.group_settlement import GroupSettlement
    from app.models.transaction_split import TransactionSplit
    from app.models.user import User


# Allowed values for Group.kind. The data model is identical across
# kinds; this is metadata that drives UI labels and (later) settlement
# workflows. Keeps the same table B2C-friendly (social) and B2B-ready
# (cost_center / project / client) without a schema change.
GROUP_KINDS = ("social", "cost_center", "project", "client", "other")

# Allowed values for TransactionSplit.share_type — kept here so models
# importing Group don't pull from the splits module.
SHARE_TYPES = ("equal", "exact", "percent")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Single owner today; will become workspace_id when multi-user
    # workspaces land. Service code never assumes
    # transaction.user_id == group.user_id — always join through this FK.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="social", server_default="social")
    default_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    icon: Mapped[str] = mapped_column(String(50), default="users", server_default="users")
    color: Mapped[str] = mapped_column(String(7), default="#6B7280", server_default="#6B7280")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship()
    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    settlements: Mapped[list["GroupSettlement"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    # Per-request transient flag set by group_service._tag_owner.
    is_owner = cast(bool, False)


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    # Nullable so shadow members (non-Securo people) work from day one.
    # SET NULL on delete preserves the shadow record + its history if
    # the linked account is removed.
    linked_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Marks the member that represents the group owner ("me"). Used to
    # compute "you owe / owes you" balances without scanning user_id on
    # every read.
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    group: Mapped["Group"] = relationship(back_populates="members")
    splits: Mapped[list["TransactionSplit"]] = relationship(back_populates="member")
