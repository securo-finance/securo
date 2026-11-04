import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "date", name="uq_fx_rate_base_quote_date"),
        Index("ix_fx_rates_quote_date", "quote_currency", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    base_currency: Mapped[str] = mapped_column(String(3))  # Always "USD" for OER
    quote_currency: Mapped[str] = mapped_column(String(3))
    date: Mapped[_date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=10))
    source: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
