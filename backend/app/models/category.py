import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category_group import CategoryGroup
    from app.models.user import User


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("category_groups.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(50), default="circle-help")
    color: Mapped[str] = mapped_column(String(7), default="#6B7280")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    # Flag for "money that moves rather than money earned or spent".
    # Transactions in these categories are excluded from income/expense
    # aggregations in dashboards and reports — same treatment as paired
    # transfers (transfer_pair_id IS NOT NULL), but category-based so we
    # can catch one-sided movements like an investment application where
    # the counterpart lives in Assets, not Accounts.
    treat_as_transfer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Flag to exclude transactions in this category from reports and dashboard aggregations.
    # When set to True, transactions with this category are treated as if they don't exist
    # for income/expense calculations.
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    user: Mapped["User"] = relationship(back_populates="categories")
    group: Mapped[Optional["CategoryGroup"]] = relationship(back_populates="categories")
