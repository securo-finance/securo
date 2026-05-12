import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    apply_to_transfer_pair: bool = False
    # CC bucketing override (issue #92). Empty string / explicit null clears
    # it back to auto. Only meaningful for credit-card accounts.
    effective_bill_date: Optional[_Date] = None
    # When provided, replaces the transaction's splits wholesale. Pass
    # an object with an empty `splits` list to clear them.
    splits: Optional[TransactionSplitsInput] = None


class TransactionRead(TransactionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    category: Optional[CategoryRead] = None
    currency: str = "USD"
    source: str
    status: str = "posted"
    payee: Optional[str] = None
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
    bill_id: Optional[uuid.UUID] = None
    effective_bill_date: Optional[_Date] = None
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

    model_config = ConfigDict(from_attributes=True)


class BulkCategorizeRequest(BaseModel):
    transaction_ids: list[uuid.UUID]
    category_id: Optional[uuid.UUID] = None


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
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal
    date: _Date
    description: str
    notes: Optional[str] = None
    fx_rate: Optional[Decimal] = None


class LinkTransferRequest(BaseModel):
    transaction_ids: list[uuid.UUID]


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


class TransactionImportPreview(BaseModel):
    transactions: list[TransactionImport]
    detected_format: str


class TransactionImportRequest(BaseModel):
    account_id: uuid.UUID
    transactions: list[TransactionImport]
    filename: str = ""
    detected_format: str = ""
    detect_duplicates: bool = True
