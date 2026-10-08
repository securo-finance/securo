import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ProductKind = Literal["service", "product"]
PriceBilling = Literal["one_time", "recurring"]
PriceInterval = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]
ProductOrigin = Literal["local", "imported"]


class PriceInput(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3)
    unit_price: Decimal = Field(..., ge=0)
    tax_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    billing: PriceBilling = "one_time"
    #: Required when `billing` is recurring, refused otherwise.
    interval: Optional[PriceInterval] = None
    nickname: Optional[str] = Field(default=None, max_length=100)
    #: A name of the workspace's own choosing, unique among its prices.
    lookup_key: Optional[str] = Field(default=None, max_length=200)
    external_source: Optional[str] = Field(default=None, max_length=50)
    external_id: Optional[str] = Field(default=None, max_length=255)


class PriceUpdate(BaseModel):
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    unit_price: Optional[Decimal] = Field(default=None, ge=0)
    tax_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    billing: Optional[PriceBilling] = None
    interval: Optional[PriceInterval] = None
    nickname: Optional[str] = Field(default=None, max_length=100)
    lookup_key: Optional[str] = Field(default=None, max_length=200)
    active: Optional[bool] = None


class PriceRead(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    currency: str
    unit_price: Decimal
    tax_rate: Optional[Decimal] = None
    billing: PriceBilling
    interval: Optional[PriceInterval] = None
    nickname: Optional[str] = None
    lookup_key: Optional[str] = None
    active: bool
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    kind: ProductKind = "service"
    unit: Optional[str] = Field(default=None, max_length=20)
    custom_fields: Optional[dict[str, Any]] = None
    #: Fiscal references keyed as the jurisdiction pack suggests
    #: (`ncm`, `service_code`, `hs_code`...), any key accepted.
    fiscal_refs: Optional[dict[str, str]] = None
    #: Created with the product, so "a product with a price" is one call.
    prices: list[PriceInput] = []
    origin: Optional[ProductOrigin] = None
    external_source: Optional[str] = Field(default=None, max_length=50)
    external_id: Optional[str] = Field(default=None, max_length=255)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    kind: Optional[ProductKind] = None
    unit: Optional[str] = Field(default=None, max_length=20)
    custom_fields: Optional[dict[str, Any]] = None
    fiscal_refs: Optional[dict[str, str]] = None
    #: False archives: gone from the picker, still readable on invoices.
    active: Optional[bool] = None


class ProductRead(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    kind: ProductKind
    unit: Optional[str] = None
    active: bool
    origin: str
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None
    fiscal_refs: Optional[dict[str, str]] = None
    prices: list[PriceRead] = []
    created_at: datetime
    #: Derived: how many invoices name this product. Filled by the
    #: router from one grouped query, never stored.
    invoice_count: int = 0

    model_config = ConfigDict(from_attributes=True)
