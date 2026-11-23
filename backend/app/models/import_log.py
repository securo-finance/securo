import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.transaction import Transaction


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Null for an order import: those land on holdings, not on an account.
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    #: What was imported: "transactions" (a bank statement) or "asset_orders".
    entity: Mapped[str] = mapped_column(String(20), default="transactions", server_default="transactions")
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(10))
    #: Rows written by this import, whichever kind they are.
    transaction_count: Mapped[int] = mapped_column(Integer)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    total_debit: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    #: The OFX rows of this import were dated on the UTC day ofxparse gave,
    #: before #1671 kept the day the bank printed. Only those rows may sit one
    #: day off their FITID's real date, so only they get the one-day window
    #: when a later statement is deduplicated.
    ofx_utc_dates: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    account: Mapped[Optional["Account"]] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="import_log")
