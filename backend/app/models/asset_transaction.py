import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.asset import Asset


class AssetTransaction(Base):
    """A single buy/sell entry in an asset's ledger.

    Transactions are the source of truth for a market-priced holding: the
    asset's current `units`, `average_price` (preço médio) and cost basis are
    derived by replaying the ledger in date order (see
    `asset_transaction_service.recompute_position`). This is what powers
    ticker-grouped holdings + a separate transactions view (issue #235).
    """

    __tablename__ = "asset_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # buy, sell
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=6))
    price: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=6))  # per-share, asset currency
    fee: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual, import, pluggy
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped["Asset"] = relationship(back_populates="transactions")
