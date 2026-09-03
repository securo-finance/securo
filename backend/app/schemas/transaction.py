import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.category import CategoryRead
from app.schemas.transaction_split import (
    TransactionSplitInput,
    TransactionSplitRead,
    TransactionSplitsInput,
)


class TransactionBase(BaseModel):
    description: str
    amount: Decimal
    date: _Date
    type: str  # debit, credit
    external_id: Optional[str] = None
    currency: Optional[str] = None
    fx_rate: Optional[Decimal] = None
    payee_raw: Optional[str] = None  # raw payee string from import (OFX/QIF)


class TransactionCreate(TransactionBase):
    account_id: uuid.UUID
    category_id: Optional[uuid.UUID] = None
    payee_id: Optional[uuid.UUID] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    amount_primary: Optional[Decimal] = None
    fx_rate_used: Optional[Decimal] = None
    effective_bill_date: Optional[_Date] = None
    splits: Optional[TransactionSplitsInput] = None
    # Manual status override. When omitted the transaction is created as
    # "posted" (settled), matching the model default. Pass "pending"
    # (not yet settled) to record an entry that isn't settled yet. Only
    # posted/pending are valid.
    status: Optional[Literal["posted", "pending"]] = None
    # Manual installment metadata. When present, this transaction is one
    # installment of a manually-created series (created via the regular
    # endpoint, e.g. a single installment, or via the series endpoint
    # which fans out a payload into N rows). All four fields must be set
    # together (validated below).
    installment_number: Optional[int] = Field(default=None, ge=1)
    total_installments: Optional[int] = Field(default=None, ge=1)
    installment_total_amount: Optional[Decimal] = None
    installment_purchase_date: Optional[_Date] = None

    @model_validator(mode="after")
    def validate_installment_fields(self):
        installment_number = self.installment_number
        total_installments = self.total_installments
        installment_total_amount = self.installment_total_amount
        installment_purchase_date = self.installment_purchase_date
        provided = [
            installment_number is not None,
            total_installments is not None,
            installment_total_amount is not None,
            installment_purchase_date is not None,
        ]
        if any(provided) and not all(provided):
            raise ValueError(
                "installment_number, total_installments, installment_total_amount and "
                "installment_purchase_date must be provided together"
            )
        if (
            installment_number is not None
            and total_installments is not None
            and installment_total_amount is not None
            and installment_purchase_date is not None
        ):
            if installment_number > total_installments:
                raise ValueError(
                    "installment_number must be between 1 and total_installments"
                )
            if installment_total_amount <= 0:
                raise ValueError("installment_total_amount must be positive")
            # The purchase date is the date of the first installment, so a
            # given installment can never predate it.
            if installment_purchase_date > self.date:
                raise ValueError(
                    "installment_purchase_date cannot be after the transaction date"
                )
        return self


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    date: Optional[_Date] = None
    type: Optional[str] = None
    currency: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    payee_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    amount_primary: Optional[Decimal] = None
    fx_rate_used: Optional[Decimal] = None
    is_ignored: Optional[bool] = None
    exclude_from_pnl: Optional[bool] = None
    # Manual status override (posted=settled, pending=not yet settled). Lets the
    # user mark a manually-entered transaction as settled once it clears,
    # or flip a synced row back to pending before the next sync.
    status: Optional[Literal["posted", "pending"]] = None
    apply_to_transfer_pair: bool = False
    # CC bucketing override (issue #92). Empty string / explicit null clears
    # it back to auto. Only meaningful for credit-card accounts.
    effective_bill_date: Optional[_Date] = None
    # When provided, replaces the transaction's splits wholesale. Pass
    # an object with an empty `splits` list to clear them.
    splits: Optional[TransactionSplitsInput] = None
    # Installment-series scope for edits. "this" (default) only touches the
    # target row; "future" touches it plus all later installments of the
    # same series; "all" touches every row in the series. Ignored when the
    # transaction has no installment fingerprint.
    apply_to: Literal["this", "future", "all"] = "this"


class InstallmentSeriesCreate(BaseModel):
    """Payload for POST /api/transactions/installments — repeats a purchase
    as ``installments`` equal payments."""

    base: TransactionCreate
    installments: int = Field(ge=2, le=360, description="Number of parcels (>= 2)")
    # Status of the first installment; subsequent ones are created "pending".
    first_installment_status: Literal["posted", "pending"] = "posted"
    # Period between installments. Defaults to monthly. Matches the
    # recurring-transaction frequencies so "repeat as installments" offers
    # the same cadence choices as a recurring bill.
    frequency: Literal["monthly", "quarterly", "weekly", "yearly"] = "monthly"

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.base.amount <= 0:
            raise ValueError("amount must be positive")
        return self


class TransactionInvoiceLink(BaseModel):
    """The invoice this transaction settles, when it settles one.

    Present only for workspaces with the invoicing module, and only on
    rows that carry an allocation — a personal workspace never sees this
    field at all, because the query that fills it is not run there.
    """

    invoice_id: uuid.UUID
    number: Optional[int] = None
    series: Optional[str] = None
    #: The name an imported invoice arrived with. Without it the badge for
    #: one has nothing to show, since it carries no number of ours.
    external_number: Optional[str] = None
    amount: Decimal


class TransactionRead(TransactionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    category: Optional[CategoryRead] = None
    #: Every invoice this transaction settles. A list because one
    #: payment can settle several — a payout net of fees is the
    #: ordinary case, not the exotic one.
    invoice_links: list[TransactionInvoiceLink] = []
    currency: str = "USD"
    source: str
    status: str = "posted"
    payee: Optional[str] = None
    original_description: Optional[str] = None
    payee_id: Optional[uuid.UUID] = None
    payee_name: Optional[str] = None
    notes: Optional[str] = None
    transfer_pair_id: Optional[uuid.UUID] = None
    amount_primary: Optional[float] = None
    fx_rate_used: Optional[float] = None
    fx_fallback: bool = False
    attachment_count: int = 0
    installment_number: Optional[int] = None
    total_installments: Optional[int] = None
    installment_total_amount: Optional[float] = None
    installment_purchase_date: Optional[_Date] = None
    installment_series_id: Optional[uuid.UUID] = None
    bill_id: Optional[uuid.UUID] = None
    effective_bill_date: Optional[_Date] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    splits: list[TransactionSplitRead] = []
    # Shared-transaction view fields. Set per-request when the viewer
    # is a linked member of one of this transaction's splits but not
    # its owner. The viewer sees their share amount instead of the
    # parent's full amount, with a back-link to the originating group.
    is_shared: bool = False
    viewer_share: Optional[Decimal] = None
    group_id: Optional[uuid.UUID] = None
    # Display name of the parent's owner — derived from the group's
    # is_self member at request time. Helps the UI show who paid
    # instead of a generic "shared" badge.
    parent_owner_name: Optional[str] = None
    is_ignored: bool = False
    exclude_from_pnl: bool = False

    @model_validator(mode="after")
    def reflect_ignored_category(self):
        if self.category and self.category.is_ignored:
            self.is_ignored = True
        return self

    model_config = ConfigDict(from_attributes=True)


class BulkCategorizeRequest(BaseModel):
    transaction_ids: list[uuid.UUID]
    category_id: Optional[uuid.UUID] = None


class TransactionBulkDeleteRequest(BaseModel):
    transaction_ids: list[uuid.UUID]


class BulkAddToGroupRequest(BaseModel):
    transaction_ids: list[uuid.UUID]
    group_id: uuid.UUID
    # Bulk supports `equal` and `percent` only — `exact` amounts can't
    # generalize across transactions of different totals.
    share_type: Literal["equal", "percent"] = "equal"
    # Subset of group members to include. Each entry's `share_pct` is
    # required when share_type='percent' (and must sum to 100). For
    # share_type='equal' only `group_member_id` is read.
    member_splits: list[TransactionSplitInput] = Field(default_factory=list)


class TransferCreate(BaseModel):
    # `extra="forbid"` so the removed `fx_rate` field fails loudly instead of
    # being dropped silently: a client still sending a rate would otherwise get
    # a transfer converted at the market rate without any hint that its input
    # was ignored.
    model_config = ConfigDict(extra="forbid")

    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal
    # Amount that actually landed on the destination account, for
    # cross-currency transfers. Left blank, the destination is converted at the
    # market rate for `date`.
    destination_amount: Optional[Decimal] = Field(default=None, gt=0)
    date: _Date
    description: str
    notes: Optional[str] = None


class LinkTransferRequest(BaseModel):
    transaction_ids: list[uuid.UUID]


class CreateCounterpartRequest(BaseModel):
    """Mark a transaction as a transfer by auto-creating its counterpart in
    another account. Used when the counterpart account is manual, so no
    matching transaction exists to link against."""
    to_account_id: uuid.UUID


class BulkTagsRequest(BaseModel):
    transaction_ids: list[uuid.UUID]
    tags: list[str]


class TransferRead(BaseModel):
    debit: TransactionRead
    credit: TransactionRead
    transfer_pair_id: uuid.UUID


class TransactionImport(TransactionBase):
    """TransactionBase extended with import-only fields not exposed in read responses."""
    category_name: Optional[str] = None
    suggested_category_id: Optional[uuid.UUID] = None
    suggested_category_name: Optional[str] = None
    excluded: bool = False
    category_id: Optional[uuid.UUID] = None
    force_uncategorized: bool = False
    notes: Optional[str] = None


class TransactionImportPreview(BaseModel):
    transactions: list[TransactionImport]
    detected_format: str
    # CSV header column names, exposed so the UI can offer column-mapping
    # dropdowns. Empty for non-CSV formats.
    csv_columns: list[str] = []
    # Set when a CSV's columns could not be auto-detected. The preview still
    # succeeds (with no transactions) so the UI can show the mapping dropdowns.
    parse_error: Optional[str] = None


class TransactionImportRequest(BaseModel):
    account_id: uuid.UUID
    transactions: list[TransactionImport]
    filename: str = ""
    detected_format: str = ""
    detect_duplicates: bool = True
