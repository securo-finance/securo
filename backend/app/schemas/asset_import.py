from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AssetOrderImport(BaseModel):
    """One buy or sell read from the file, before it reaches a holding."""

    row: int  # 1-based line in the source file, so an error can point at it
    ticker: str
    date: date
    kind: str  # buy | sell
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    currency: Optional[str] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    external_id: Optional[str] = None


class AssetImportRowError(BaseModel):
    """A row that will not be imported, and why, named by its line number."""

    row: int
    reason: str  # missing_ticker | invalid_date | invalid_quantity | invalid_price | invalid_kind | unknown_ticker | oversell
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
