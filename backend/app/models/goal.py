import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.asset import Asset
    from app.models.asset_group import AssetGroup
    from app.models.user import User
    from app.models.transaction import Transaction


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    current_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    initial_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    target_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    current_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    tracking_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual, account, asset, asset_group, net_worth
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    asset_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, paused, archived
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()
    account: Mapped[Optional["Account"]] = relationship()
    asset: Mapped[Optional["Asset"]] = relationship()
    asset_group: Mapped[Optional["AssetGroup"]] = relationship()
    allocations: Mapped[list["GoalAllocation"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", passive_deletes=True
    )


class GoalAllocation(Base):
    """A virtual reservation inside an existing account.

    Positive amounts reserve money for a pocket; negative amounts release it.
    Transaction-backed rows inherit their direction from the transaction type.
    They never create or alter a financial transaction themselves.
    """

    __tablename__ = "goal_allocations"
    __table_args__ = (
        Index("ix_goal_allocations_workspace_id", "workspace_id"),
        Index("ix_goal_allocations_goal_id", "goal_id"),
        Index("ix_goal_allocations_transaction_id", "transaction_id"),
        UniqueConstraint(
            "goal_id", "transaction_id", name="uq_goal_allocations_goal_transaction"
        ),
        CheckConstraint("amount <> 0", name="ck_goal_allocations_amount_nonzero"),
        CheckConstraint(
            "source IN ('opening', 'adjustment', 'transaction')",
            name="ck_goal_allocations_source",
        ),
        CheckConstraint(
            "(source = 'transaction' AND transaction_id IS NOT NULL) OR "
            "(source IN ('opening', 'adjustment') AND transaction_id IS NULL)",
            name="ck_goal_allocations_source_transaction",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    goal: Mapped["Goal"] = relationship(back_populates="allocations", lazy="joined")
    transaction: Mapped[Optional["Transaction"]] = relationship(
        back_populates="goal_allocations"
    )

    @property
    def goal_name(self) -> str:
        return self.goal.name
