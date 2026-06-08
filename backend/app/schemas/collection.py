import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CollectionBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str = "folder"
    color: str = "#6366F1"
    position: int = 0


class CollectionCreate(CollectionBase):
    # Accounts and wallets (asset_groups) to include. Empty is allowed.
    account_ids: list[uuid.UUID] = []
    wallet_ids: list[uuid.UUID] = []


class CollectionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    icon: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None
    # When provided, replaces that membership wholesale. None leaves it untouched.
    account_ids: Optional[list[uuid.UUID]] = None
    wallet_ids: Optional[list[uuid.UUID]] = None


class CollectionRead(CollectionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    account_ids: list[uuid.UUID] = []
    account_count: int = 0
    # Asset wallets (asset_groups) in this collection.
    wallet_ids: list[uuid.UUID] = []
    wallet_count: int = 0

    model_config = ConfigDict(from_attributes=True)
