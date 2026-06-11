import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.asset_group import AssetGroup
    from app.models.asset_transaction import AssetTransaction
    from app.models.asset_value import AssetValue


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(50))  # real_estate, vehicle, valuable, investment, other
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    units: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=6), nullable=True)
    valuation_method: Mapped[str] = mapped_column(String(20), default="manual")  # manual, growth_rule
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    sell_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sell_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    growth_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # percentage, absolute
    growth_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=6), nullable=True)
    growth_frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # daily, weekly, monthly, yearly
    growth_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    purchase_price_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    # Provider-agnostic sync fields. `source` tags where the asset came from
    # ("manual", "pluggy", ...). `external_id` is the source's stable ID for
    # the same logical holding across syncs. `connection_id` links back to the
    # provider connection when the source requires authentication.
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_connections.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    external_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Optional parent group ("wallet"). NULL means ungrouped. Deleting a
    # group nullifies this field rather than removing the asset.
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True
    )

    # Market-priced assets (valuation_method="market_price"). `ticker` is the
    # Yahoo Finance symbol (AAPL, BTC-USD, PETR4.SA). `last_price` is the
    # most recent quote cached on the row so list views don't re-hit yfinance.
    ticker: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ticker_exchange: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=18, scale=6), nullable=True
    )
    last_price_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Weighted-average cost per unit (preço médio), derived from the
    # asset_transactions ledger and cached here for cheap list reads. For
    # ledger-backed holdings `purchase_price` caches the total cost basis of
    # the currently-held units, so `gain_loss = current_value - purchase_price`
    # stays meaningful as the unrealized gain (issue #235).
    average_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=18, scale=6), nullable=True
    )
    # Cumulative realized gain/loss from sell transactions, in asset currency.
    realized_gain: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=18, scale=2), nullable=True
    )
    # Fully-formed logo URL — populated at create time for market-priced
    # assets when a logo provider is configured. Null means "no logo, use
    # the type icon". Frontend swaps to the type icon on <img> load error.
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    values: Mapped[list["AssetValue"]] = relationship(back_populates="asset", cascade="all, delete-orphan")
    transactions: Mapped[list["AssetTransaction"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    group: Mapped[Optional["AssetGroup"]] = relationship(back_populates="assets")
