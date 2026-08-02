import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.category import CategoryRead


class PayeeCreate(BaseModel):
    name: str = Field(..., max_length=255)
    type: str = "merchant"
    notes: Optional[str] = Field(None, max_length=1000)


class PayeeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    type: Optional[str] = None
    is_favorite: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=1000)


class PayeeRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    type: str
    is_favorite: bool
    notes: Optional[str] = None
    created_at: datetime
    transaction_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class PayeeSummary(BaseModel):
    payee: PayeeRead
    total_spent: Decimal = Decimal("0")
    total_received: Decimal = Decimal("0")
    transaction_count: int = 0
    most_common_category: Optional[CategoryRead] = None
    last_transaction_date: Optional[_Date] = None


class PayeeMergeRequest(BaseModel):
    source_ids: list[uuid.UUID]
    target_id: uuid.UUID
