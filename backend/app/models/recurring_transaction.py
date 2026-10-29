import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.category import Category
    from app.models.transaction import Transaction
    from app.models.user import User


class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "description", "frequency", "start_date", name="uq_recurring_tx"),
        CheckConstraint(
            "weekend_adjustment IN ('none', 'previous_friday', 'next_monday')",
            name="ck_recurring_transactions_weekend_adjustment",
        ),
        CheckConstraint(
            "business_day IS NULL OR (business_day >= 1 AND business_day <= 23)",
            name="ck_recurring_transactions_business_day",
        ),
        CheckConstraint(
            "grace_days >= 0 AND grace_days <= 15",
            name="ck_recurring_transactions_grace_days",
        ),
        CheckConstraint(
            "amount_tolerance_percent >= 0 AND amount_tolerance_percent <= 50",
            name="ck_recurring_transactions_amount_tolerance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    type: Mapped[str] = mapped_column(String(10))  # debit, credit
    frequency: Mapped[str] = mapped_column(String(20))  # weekly, biweekly, monthly, quarterly, semiannual, yearly
    weekend_adjustment: Mapped[str] = mapped_column(
        String(20), default="none", server_default="none"
    )
    day_of_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # When set, each occurrence falls on the n-th business day of its month
    # (a salary "on the 5th business day") instead of on day_of_month. The
    # nominal pointer then sits on the 1st and only the effective date moves.
    business_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Count Saturdays toward business_day, as the Brazilian labour rule does.
    # An occurrence that lands on a Saturday is then moved by
    # weekend_adjustment, since no money moves on it.
    count_saturdays: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Holiday calendar: a country code with an optional subdivision ("BR",
    # "BR-SP", "US-NY"). Holidays are not business days, and
    # weekend_adjustment moves an occurrence off them too. The schema checks
    # the code against app.services.business_days; the list is too long and
    # changes too often to hold in a CHECK constraint.
    holiday_calendar: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # How many days late the charge may arrive and still be this occurrence
    # (rent due on the 5th that may be paid on the 6th). Widens the matching
    # window after the due date; the due date itself does not move.
    grace_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Comma-separated words the bank prints for this charge ("Transferência
    # Recebida" for a salary). A charge whose description contains one is
    # treated as describing this bill, whatever the bill itself is called.
    match_keywords: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # How far, in percent, the charge may differ from ``amount`` and still be
    # this bill (a rent that carries the month's utilities). Zero is exact.
    amount_tolerance_percent: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=2), default=0, server_default="0"
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When True (the default), generate_pending materializes this bill's due
    # occurrences into real transactions. When False, no placeholder is written
    # and the bill is only shown as a projection until the actual charge is
    # matched to it (e.g. from bank sync). Either way, incoming real
    # transactions are linked back to the bill to avoid duplicates.
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    next_occurrence: Mapped[date] = mapped_column(Date)
    amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    fx_rate_used: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=20, scale=10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship()
    account: Mapped[Optional["Account"]] = relationship()
    category: Mapped[Optional["Category"]] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="recurring_transaction"
    )
