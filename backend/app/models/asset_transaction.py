import uuid
from datetime import date as _date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.asset import Asset


class AssetTransaction(Base):
    """A single buy/sell/split entry in an asset's ledger.

    Transactions are the source of truth for a market-priced holding: the
    asset's current `units`, `average_price` (preço médio) and cost basis are
    derived by replaying the ledger in date order (see
    `asset_transaction_service.recompute_position`). This is what powers
    ticker-grouped holdings + a separate transactions view (issue #235).

    A split changes how many units there are from its date on (`old_units`
    became `new_units`) without moving any money, so the cost basis stays and
    the average price moves by the inverse ratio (issue #944).
    """

    __tablename__ = "asset_transactions"
    __table_args__ = (
        # NULL passes a CHECK, so the IS NOT NULLs are what reject a split with no ratio.
        CheckConstraint(
            "(kind = 'split' AND old_units IS NOT NULL AND new_units IS NOT NULL"
            " AND old_units > 0 AND new_units > 0)"
            " OR (kind <> 'split' AND old_units IS NULL AND new_units IS NULL)",
            name="ck_asset_transactions_split_units",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # buy, sell, split
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=6))
    price: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=6))  # per-share, asset currency
    fee: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    date: Mapped[_date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual, import, pluggy
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Split only: the `old_units` held before the split became `new_units` after
    # it, so a 100-for-1 split is 1 → 100 and a 1-for-10 reverse split is 10 → 1.
    # NULL on buys and sells. A split itself stores 0 quantity, price and fee.
    old_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: The import that wrote this row, so deleting that import can take it
    #: back out again.
    import_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_logs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped["Asset"] = relationship(back_populates="transactions")
