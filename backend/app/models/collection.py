import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.asset_group import AssetGroup
    from app.models.user import User


# Many-to-many: a collection bundles accounts; an account can belong to many
# collections. Rows cascade-delete with either side so deleting a collection
# or an account never leaves dangling membership.
collection_accounts = Table(
    "collection_accounts",
    Base.metadata,
    Column(
        "collection_id",
        UUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "account_id",
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# Many-to-many: a collection can also bundle asset "wallets" (asset_groups),
# so net-worth/reports for the collection include those wallets' assets.
# Individual assets aren't added directly — only wallets — which keeps
# membership stable as assets flow in/out of a wallet.
collection_asset_groups = Table(
    "collection_asset_groups",
    Base.metadata,
    Column(
        "collection_id",
        UUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "asset_group_id",
        UUID(as_uuid=True),
        ForeignKey("asset_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Collection(Base):
    """A user-defined, named group of accounts used to filter the app's
    views (dashboard, reports, transactions) — issue #105.

    Cross-cutting and many-to-many: an account can belong to several
    collections simultaneously. Workspace-scoped. Deliberately distinct from
    expense-splitting "Groups" (people) and asset "Wallets" (asset_groups):
    this is a reporting/filter lens over the accounts you already have.
    """

    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(50), default="folder")
    color: Mapped[str] = mapped_column(String(7), default="#6366F1")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Eager-load members so reads can expose account_ids without an N+1.
    accounts: Mapped[list["Account"]] = relationship(
        secondary=collection_accounts, lazy="selectin"
    )
    asset_groups: Mapped[list["AssetGroup"]] = relationship(
        secondary=collection_asset_groups, lazy="selectin"
    )
    user: Mapped["User"] = relationship()
