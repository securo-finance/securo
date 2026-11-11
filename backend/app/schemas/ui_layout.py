from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The surfaces someone can arrange from the library.
Surface = Literal["dashboard", "reports"]

#: A library item: a built-in id (`balance-flow`) or, later, a plugin's
#: (`plugin:owner/name:key`). The frontend owns the registry.
ITEM_ID_PATTERN = r"^[a-z0-9][a-z0-9:/_.-]{0,159}$"
MAX_ITEMS = 40


class LayoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=ITEM_ID_PATTERN)


class LayoutWrite(BaseModel):
    items: list[LayoutItem] = Field(max_length=MAX_ITEMS)

    @field_validator("items")
    @classmethod
    def _unique(cls, items: list[LayoutItem]) -> list[LayoutItem]:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("an item can appear only once")
        return items


class LayoutsRead(BaseModel):
    """Each surface as arranged, or null for Securo's default."""

    dashboard: list[LayoutItem] | None = None
    reports: list[LayoutItem] | None = None
