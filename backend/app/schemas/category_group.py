import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.category import CategoryRead

CategoryFlowType = Literal["income", "expense"]


class CategoryGroupBase(BaseModel):
    name: str
    icon: str = "folder"
    color: str = "#6B7280"
    flow_type: CategoryFlowType = "expense"
    position: int = 0


class CategoryGroupCreate(CategoryGroupBase):
    pass


class CategoryGroupUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    flow_type: Optional[CategoryFlowType] = None
    position: Optional[int] = None


class CategoryGroupRead(CategoryGroupBase):
    id: uuid.UUID
    user_id: uuid.UUID
    is_system: bool
    categories: list[CategoryRead] = []

    model_config = ConfigDict(from_attributes=True)
