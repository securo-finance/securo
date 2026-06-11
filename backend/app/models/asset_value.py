import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class AssetValue(Base):
    __tablename__ = "asset_values"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=6))
    # Per-share price on `date` for market-priced holdings (quantity-independent).
    # The value chart is rebuilt as ledger_quantity(date) × price(date) so that
    # entering past buys/sells correctly reshapes the whole history (issue:
    # backdated trades didn't update the baked `amount`). Null for manual/growth
    # assets, where `amount` is the value directly.
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=18, scale=6), nullable=True)
    date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual, rule, sync

    asset: Mapped["Asset"] = relationship(back_populates="values")
