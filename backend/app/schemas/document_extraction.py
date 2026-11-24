import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.schemas.transaction import TransactionImport


class DocumentAiStatus(BaseModel):
    """What this workspace can read, for the UI to say before an upload."""

    # True once an owner picked a connection that still works for it.
    enabled: bool
    configured: bool
    connection_id: Optional[uuid.UUID] = None
    connection_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    vision: Literal["yes", "no", "unknown"] = "unknown"
    vision_source: Literal["setting", "provider", "catalog", "none"] = "none"
    max_file_size_mb: int
    max_pages: int


class DocumentAiSettingsRead(BaseModel):
    connection_id: Optional[uuid.UUID] = None
    # Replaces the connection's default model when set.
    model: Optional[str] = None
    vision: str = "auto"


class DocumentAiSettingsUpdate(BaseModel):
    # One of the owner's own agents connections; None turns reading off.
    connection_id: Optional[uuid.UUID] = None
    model: Optional[str] = None
    vision: str = "auto"


class BalanceCheck(BaseModel):
    opening_balance: str
    closing_balance: str
    computed_closing_balance: str
    difference: str
    ok: bool


class ReceiptMatch(BaseModel):
    transaction_id: uuid.UUID
    date: str
    description: str
    amount: str
    currency: str
    type: str
    account_id: uuid.UUID
    account_name: str
    attachments: int


class DocumentExtractionRead(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    filename: str
    content_type: str
    size: int
    provider: Optional[str] = None
    model: Optional[str] = None
    method: Optional[str] = None
    pages: Optional[int] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    transaction_id: Optional[uuid.UUID] = None
    import_log_id: Optional[uuid.UUID] = None
    # Statements: the rows, as the import preview gives them.
    statement: Optional[dict[str, Any]] = None
    transactions: list[TransactionImport] = []
    balance_check: Optional[BalanceCheck] = None
    suggested_account_id: Optional[uuid.UUID] = None
    dropped: int = 0
    truncated: bool = False
    # Scanned pages the model could not read (it takes no images).
    unread_pages: list[int] = []
    # Receipts: what was read, transactions it may be, a category for a new one.
    receipt: Optional[dict[str, Any]] = None
    matches: list[ReceiptMatch] = []
    suggested_category_id: Optional[uuid.UUID] = None
    # False when a transaction was created from the receipt but its file
    # could not be stored with it; the transaction is there either way.
    attached: Optional[bool] = None


class AttachReceiptRequest(BaseModel):
    transaction_id: uuid.UUID
