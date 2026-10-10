import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CategoryAllocationInput(BaseModel):
    """One row in a category-allocations payload."""

    category_id: uuid.UUID
    amount: Decimal
    notes: Optional[str] = None


class CategoryAllocationsInput(BaseModel):
    """Whole category-allocations payload attached to a transaction."""

    allocations: list[CategoryAllocationInput]


class CategoryAllocationRead(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    notes: Optional[str] = None
    position: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
