import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CategoryBase(BaseModel):
    name: str
    icon: str = "circle-help"
    color: str = "#6B7280"


class CategoryCreate(CategoryBase):
    group_id: Optional[uuid.UUID] = None
    has_budget: bool = False
    budget_amount: Optional[Decimal] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    group_id: Optional[uuid.UUID] = None
    has_budget: Optional[bool] = None
    budget_amount: Optional[Decimal] = None


class CategoryRead(CategoryBase):
    id: uuid.UUID
    user_id: uuid.UUID
    group_id: Optional[uuid.UUID] = None
    is_system: bool
    has_budget: bool
    budget_amount: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)
