import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.category import ICON_MAX_LENGTH, CategoryRead


class CategoryGroupBase(BaseModel):
    name: str
    icon: str = Field("folder", max_length=ICON_MAX_LENGTH)
    color: str = "#6B7280"
    position: int = 0


class CategoryGroupCreate(CategoryGroupBase):
    pass


class CategoryGroupUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=ICON_MAX_LENGTH)
    color: Optional[str] = None
    position: Optional[int] = None
    is_hidden: Optional[bool] = None


class CategoryGroupRead(CategoryGroupBase):
    id: uuid.UUID
    user_id: uuid.UUID
    is_system: bool
    is_hidden: bool = False
    categories: list[CategoryRead] = []

    model_config = ConfigDict(from_attributes=True)
