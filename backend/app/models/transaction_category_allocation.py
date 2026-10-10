import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.transaction import Transaction


class TransactionCategoryAllocation(Base):
    __tablename__ = "transaction_category_allocations"

    __table_args__ = (
        # A category appears at most once per transaction.
        UniqueConstraint(
            "transaction_id", "category_id", name="uq_txn_cat_alloc_tx_category"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NO ondelete action: mirrors transactions.category_id (NO ACTION).
    # Deletion of a category with active allocations is blocked by FK
    # constraint; category_service.delete_category guards this via
    # get_category_usage() before attempting the delete.
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=False,
        index=True,
    )
    # Amount in the parent transaction's currency. The service layer assigns
    # the rounding residual to the last row so the sum is exact.
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Preserves row order; the residual rule needs a stable "last" row.
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="category_allocations")
    category: Mapped[Optional["Category"]] = relationship()
