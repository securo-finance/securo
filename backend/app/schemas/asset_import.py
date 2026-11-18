from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AssetOrderImport(BaseModel):
    """One buy, sell or split read from the file, before it reaches a holding."""

    row: int  # 1-based line in the source file, so an error can point at it
    ticker: str
    date: date
    kind: str  # buy | sell | split
    quantity: Decimal  # 0 on a split
    price: Decimal  # 0 on a split
    fee: Decimal = Decimal("0")
    currency: Optional[str] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    external_id: Optional[str] = None
    # Split only: `old_units` held before it became `new_units` after it.
    old_units: Optional[int] = None
    new_units: Optional[int] = None


class AssetImportRowError(BaseModel):
    """A row that will not be imported, and why, named by its line number."""

    row: int
    # missing_ticker | invalid_date | invalid_quantity | invalid_price | invalid_fee | invalid_kind
    # | invalid_split | unknown_ticker | oversell | split_not_allowed | split_in_future
    # | nothing_held_before_split | too_large
    reason: str
    ticker: Optional[str] = None
    detail: Optional[str] = None


class AssetImportWarning(BaseModel):
    """Not a reason to refuse a row, but something to see before confirming."""

    ticker: str
    reason: str  # exists_in_other_wallet | orders_already_in_other_wallet
    wallet: Optional[str] = None


class AssetImportPreview(BaseModel):
    orders: list[AssetOrderImport]
    errors: list[AssetImportRowError] = []
    warnings: list[AssetImportWarning] = []
    csv_columns: list[str] = []
    #: Set when the file could not be read at all, so the UI can show the
    #: mapping dropdowns instead of an empty preview.
    parse_error: Optional[str] = None
    #: What the import would do, without doing it.
    holdings_created: int = 0
    holdings_matched: int = 0
    skipped: int = 0


class AssetImportRequest(BaseModel):
    orders: list[AssetOrderImport]
    group_id: Optional[UUID] = None
    #: Only for the history entry, so a past import is recognisable.
    filename: Optional[str] = None


class AssetImportResult(BaseModel):
    import_log_id: Optional[UUID] = None
    imported: int
    skipped: int
    holdings_created: int
    holdings_matched: int
    errors: list[AssetImportRowError] = []
    warnings: list[AssetImportWarning] = []
