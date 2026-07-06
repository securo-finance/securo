import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, JSON, Numeric, SmallInteger, String, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.category import Category
    from app.models.credit_card_bill import CreditCardBill
    from app.models.import_log import ImportLog
    from app.models.payee import Payee
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.transaction_attachment import TransactionAttachment
    from app.models.transaction_split import TransactionSplit


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Provider's transaction ID
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    date: Mapped[_date] = mapped_column(Date)
    # Effective date for cash-flow reporting. For regular accounts this equals
    # `date`. For credit card transactions it's the due date of the bill that
    # the transaction belongs to — so accrual-mode aggregations count the
    # purchase when it hits the user's cash, not when it was made.
    effective_date: Mapped[_date] = mapped_column(Date, index=True)
    type: Mapped[str] = mapped_column(String(10))  # debit, credit
    source: Mapped[str] = mapped_column(String(20))  # sync, ofx, csv, manual
    status: Mapped[str] = mapped_column(String(10), default="posted")  # posted, pending
    payee: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    payee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("payees.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    import_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("import_logs.id"), nullable=True)
    transfer_pair_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    fx_rate_used: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=20, scale=10), nullable=True)
    # Installment (parcelamento) metadata. Populated from provider data when available.
    # `installment_number` is 1-indexed (e.g. 3 for "3/12"). Storing alongside the
    # raw tx row so the door stays open to a plan view or manual entry later
    # without another migration.
    installment_number: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    total_installments: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    installment_total_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True
    )
    installment_purchase_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    # User-set manual override for which bill cycle this tx belongs to. Null
    # by default; only meaningful for credit-card accounts. When set, beats
    # both Pluggy's billId and the cycle-math fallback (issue #92, the
    # LucasFidelis cycle_override request).
    effective_bill_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    # FK to the bill this transaction belongs to (issue #92). Set during sync
    # for providers that expose a bills feed; null otherwise — in which case
    # the read path falls back to locally-computed cycle math via
    # apply_effective_date. ON DELETE SET NULL: if a bill row is removed
    # (re-sync, deletion), the transaction stays put and just unlinks.
    bill_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Flag to exclude this transaction from reports and dashboard aggregations.
    # When set to True, the transaction is ignored for income/expense calculations.
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Link to the recurring bill this transaction fulfills (issue #116). Set when
    # a synced/imported/manual charge is matched to a recurring bill, or stamped
    # onto the placeholder generate_pending materializes. ON DELETE SET NULL: if
    # the recurring bill is removed, the transaction stays put and just unlinks.
    recurring_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recurring_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    account: Mapped["Account"] = relationship(back_populates="transactions")
    category: Mapped[Optional["Category"]] = relationship()
    bill: Mapped[Optional["CreditCardBill"]] = relationship()
    payee_entity: Mapped[Optional["Payee"]] = relationship(back_populates="transactions")
    recurring_transaction: Mapped[Optional["RecurringTransaction"]] = relationship(
        back_populates="transactions"
    )
    import_log: Mapped[Optional["ImportLog"]] = relationship(back_populates="transactions")
    attachments: Mapped[list["TransactionAttachment"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    splits: Mapped[list["TransactionSplit"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


# Safety net: `effective_date` is NOT NULL. For non-CC transactions it always
# equals `date`, so if a call site (or test) forgets to set it, fall back
# silently. CC-aware service code still calls `apply_effective_date` explicitly
# so that cycle-based due dates are stored when the account has the metadata.
@event.listens_for(Transaction, "before_insert")
def _default_effective_date(mapper, connection, target):  # type: ignore
    if target.effective_date is None:
        target.effective_date = target.date


@event.listens_for(Transaction, "before_update")
def _default_effective_date_on_update(mapper, connection, target):  # type: ignore
    if target.effective_date is None:
        target.effective_date = target.date
