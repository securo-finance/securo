import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AssetCreate(BaseModel):
    name: str
    type: str
    currency: str = "USD"
    units: Optional[Decimal] = None
    valuation_method: str = "manual"
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = None
    sell_date: Optional[date] = None
    sell_price: Optional[Decimal] = None
    current_value: Optional[Decimal] = None  # convenience: creates initial AssetValue
    growth_type: Optional[str] = None
    growth_rate: Optional[Decimal] = None
    growth_frequency: Optional[str] = None
    growth_start_date: Optional[date] = None
    is_archived: bool = False
    position: int = 0
    group_id: Optional[uuid.UUID] = None
    # Market-priced assets: ticker is enough to create one. The service
    # fetches the live quote on create and seeds the first AssetValue.
    ticker: Optional[str] = None
    ticker_exchange: Optional[str] = None
    # Per-unit price for the opening buy of a market-priced holding (preço
    # médio model, consistent with the transaction ledger). When omitted, the
    # service seeds the buy at the live quote ("bought at market now").
    unit_price: Optional[Decimal] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    currency: Optional[str] = None
    units: Optional[Decimal] = None
    valuation_method: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = None
    sell_date: Optional[date] = None
    sell_price: Optional[Decimal] = None
    growth_type: Optional[str] = None
    growth_rate: Optional[Decimal] = None
    growth_frequency: Optional[str] = None
    growth_start_date: Optional[date] = None
    is_archived: Optional[bool] = None
    position: Optional[int] = None
    # Use a sentinel to differentiate "don't change group" (field omitted)
    # from "remove from group" (explicit null). Pydantic's exclude_unset
    # already handles this via model_dump.
    group_id: Optional[uuid.UUID] = None
    ticker: Optional[str] = None
    ticker_exchange: Optional[str] = None


class AssetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    type: str
    currency: str
    units: Optional[float] = None
    valuation_method: str
    purchase_date: Optional[date] = None
    purchase_price: Optional[float] = None
    sell_date: Optional[date] = None
    sell_price: Optional[float] = None
    growth_type: Optional[str] = None
    growth_rate: Optional[float] = None
    growth_frequency: Optional[str] = None
    growth_start_date: Optional[date] = None
    is_archived: bool
    position: int
    current_value: Optional[float] = None
    current_value_primary: Optional[float] = None
    gain_loss: Optional[float] = None
    gain_loss_primary: Optional[float] = None
    value_count: int = 0
    source: str = "manual"
    connection_id: Optional[uuid.UUID] = None
    isin: Optional[str] = None
    maturity_date: Optional[date] = None
    group_id: Optional[uuid.UUID] = None
    ticker: Optional[str] = None
    ticker_exchange: Optional[str] = None
    last_price: Optional[float] = None
    last_price_at: Optional[datetime] = None
    logo_url: Optional[str] = None
    # Ledger-derived fields (issue #235). average_price = weighted-average cost
    # per unit (preço médio); total_invested = cost basis of the held units;
    # realized_gain = cumulative gain/loss from sells; transaction_count lets
    # the UI know whether a holding is ledger-backed.
    average_price: Optional[float] = None
    total_invested: Optional[float] = None
    realized_gain: Optional[float] = None
    transaction_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class AssetTransactionCreate(BaseModel):
    kind: str  # buy | sell
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    date: date
    notes: Optional[str] = None


class AssetTransactionUpdate(BaseModel):
    kind: Optional[str] = None
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    date: Optional[date] = None
    notes: Optional[str] = None


class AssetBuyCreate(BaseModel):
    """Find-or-create a ticker holding (in `group_id`) and record a buy."""

    ticker: str
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    date: date
    name: Optional[str] = None
    group_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class AssetTransactionRead(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    kind: str
    quantity: float
    price: float
    fee: float
    date: date
    source: str
    notes: Optional[str] = None
    # Denormalized holding context so the global transactions tab can render
    # rows without an extra per-row asset lookup.
    asset_name: Optional[str] = None
    ticker: Optional[str] = None
    currency: Optional[str] = None
    logo_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class MarketSymbolQuote(BaseModel):
    """Live quote for a ticker, used by the add-asset form to preview value."""

    symbol: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: str
    price: float
    quote_type: Optional[str] = None  # EQUITY, ETF, CRYPTOCURRENCY, MUTUALFUND, ...
    # Fully-formed logo URL if the provider can derive one. Caller stores
    # this verbatim on the asset; no further processing required.
    logo_url: Optional[str] = None


class MarketSymbolMatch(BaseModel):
    """A single search result returned by /assets/market/search."""

    symbol: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    quote_type: Optional[str] = None


class AssetValueCreate(BaseModel):
    amount: Decimal
    date: date


class AssetValueRead(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    amount: float
    date: date
    source: str

    model_config = ConfigDict(from_attributes=True)
