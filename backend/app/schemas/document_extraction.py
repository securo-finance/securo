import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.schemas.transaction import TransactionImport


class DocumentAiStatus(BaseModel):
    """What this workspace can read, for the UI to say before an upload."""

    enabled: bool
    configured: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    # Where the model comes from: the instance's defaults or this workspace.
    source: Optional[str] = None
    vision: Literal["yes", "no", "unknown"] = "unknown"
    vision_source: Literal["setting", "provider", "catalog", "none"] = "none"
    max_file_size_mb: int
    max_pages: int


class AdminDocumentAiSettingsRead(BaseModel):
    """The instance's document reading, as an admin sets it. Never the key."""

    enabled: bool
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    has_api_key: bool = False
    vision: str = "auto"
    configured: bool = False
    detected_vision: Literal["yes", "no", "unknown"] = "unknown"
    detected_vision_source: Literal["setting", "provider", "catalog", "none"] = "none"
    # False when SECRET_KEY is too short to seal a key saved in the app.
    can_store_secrets: bool = True


class AdminDocumentAiSettingsUpdate(BaseModel):
    enabled: bool
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    # Blank keeps the stored key; `clear_api_key` removes it.
    api_key: Optional[str] = None
    clear_api_key: bool = False
    vision: str = "auto"


class DocumentAiSettingsRead(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    has_api_key: bool = False
    vision: str = "auto"
    # The instance's defaults, shown as placeholders. Never its key.
    instance_provider: Optional[str] = None
    instance_model: Optional[str] = None
    instance_base_url: Optional[str] = None


class DocumentAiSettingsUpdate(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    # Blank keeps the stored key; `clear_api_key` removes it.
    api_key: Optional[str] = None
    clear_api_key: bool = False
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
    # Receipts: what was read, transactions it may be, a category for a new one.
    receipt: Optional[dict[str, Any]] = None
    matches: list[ReceiptMatch] = []
    suggested_category_id: Optional[uuid.UUID] = None


class AttachReceiptRequest(BaseModel):
    transaction_id: uuid.UUID
