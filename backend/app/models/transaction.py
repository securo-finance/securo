import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, Numeric, SmallInteger, String, event
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
    __table_args__ = (
        # Serves the needs-review chip count + list filter (PR-2). Composite
        # on purpose: a workspace-scoped count must not walk the whole
        # workspace's rows on a large ledger.
        Index(
            "ix_transactions_workspace_review_status",
            "workspace_id",
            "review_status",
        ),
        CheckConstraint(
            "(("
            "(review_attribution_kind IS NULL AND review_rule_id IS NULL)"
            " OR (review_attribution_kind = 'human'"
            " AND review_rule_id IS NULL AND review_status = 'reviewed'"
            " AND reviewed_at IS NOT NULL AND reviewed_by_id IS NOT NULL)"
            " OR (review_attribution_kind = 'rule'"
            " AND review_rule_id IS NOT NULL AND review_status = 'reviewed'"
            " AND reviewed_at IS NOT NULL AND reviewed_by_id IS NULL)"
            ") IS TRUE)",
            name="ck_transactions_review_attribution_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Provider's transaction ID
    description: Mapped[str] = mapped_column(String(500))
    original_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description_is_rule_managed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
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
    # True on both legs of a cross-currency transfer whose destination amount
    # the user typed in, instead of letting it be converted at the market rate.
    # Both amounts are then observed facts, so editing one leg must not
    # re-derive the other from an FX rate.
    transfer_amount_explicit: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
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
    # Stable identity for rows created by the manual installment-series
    # endpoint. Provider-synced installment rows leave this null because their
    # metadata is only a deduplication fingerprint, not a series identifier.
    installment_series_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
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
    # Ignore the row everywhere monetary totals are calculated, including the
    # account balance. The row remains stored and can still be shown in ledger
    # views unless the caller explicitly hides ignored transactions.
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Keep the row in the ledger and current balance, but leave it out of P&L
    # reports. This differs from is_ignored, which also removes the row from
    # balance calculations. Balance adjustments use this distinction so the
    # reconciled balance stays exact without looking like income or spending.
    exclude_from_pnl: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Workflow triage state (PR-2), deliberately orthogonal to the two
    # monetary flags above: review changes no computed total. Two states;
    # provenance (`reviewed_at`/`reviewed_by_id`) is written together by
    # explicit actor-driven transitions into 'reviewed' and cleared
    # together on leaving it — never exactly one stamp. Legacy and
    # exempt rows created as 'reviewed' by the DB default may keep both
    # NULL (gate §15-A1); `needs_review` always means both NULL.
    review_status: Mapped[str] = mapped_column(
        String(20), default="reviewed", server_default="reviewed"
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_transactions_reviewed_by_id", ondelete="RESTRICT", deferrable=False),
        nullable=True,
    )
    review_attribution_kind: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )
    review_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Issue #56's flag: an independent bookmark that never triggers or
    # clears review state and has zero monetary effect.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

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

    # Populated dynamically by the service (not DB columns).
    is_shared: bool = False
    viewer_share = cast(Optional[Decimal], None)
    group_id = cast(Optional[uuid.UUID], None)
    parent_owner_name = cast(Optional[str], None)
    attachment_count: int = 0
    payee_name = cast(Optional[str], None)
    running_balance = cast(Optional[float], None)
    running_balance_primary = cast(Optional[float], None)

    @property
    def review_attribution(self) -> Optional[dict[str, str | datetime | uuid.UUID | None]]:
        """Project stored attribution without inferring principals from legacy stamps."""
        if self.review_status != "reviewed":
            return None
        if self.review_attribution_kind == "human":
            return {
                "kind": "human",
                "reviewed_at": self.reviewed_at,
                "user_id": self.reviewed_by_id,
            }
        if self.review_attribution_kind == "rule":
            return {
                "kind": "rule",
                "reviewed_at": self.reviewed_at,
                "rule_id": self.review_rule_id,
            }
        return {"kind": "unknown"}


# Safety net: `effective_date` is NOT NULL. For non-CC transactions it always
# equals `date`, so if a call site (or test) forgets to set it, fall back
# silently. CC-aware service code still calls `apply_effective_date` explicitly
# so that cycle-based due dates are stored when the account has the metadata.
@event.listens_for(Transaction, "before_insert")
def _default_effective_date(mapper, connection, target):
    if target.effective_date is None:
        target.effective_date = target.date


@event.listens_for(Transaction, "before_update")
def _default_effective_date_on_update(mapper, connection, target):
    if target.effective_date is None:
        target.effective_date = target.date
