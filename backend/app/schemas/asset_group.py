import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetGroupBase(BaseModel):
    name: str
    icon: str = "wallet"
    color: str = "#0EA5E9"
    position: int = 0


class AssetGroupCreate(AssetGroupBase):
    pass


class AssetGroupUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None


class AssetGroupReorder(BaseModel):
    group_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)

    @field_validator("group_ids")
    @classmethod
    def group_ids_must_be_unique(cls, group_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group_ids must be unique")
        return group_ids


class AssetGroupRead(AssetGroupBase):
    id: uuid.UUID
    user_id: uuid.UUID
    source: str = "manual"
    connection_id: Optional[uuid.UUID] = None
    # The originating institution — preserved as context even if the user
    # renames the wallet to something like "Renda Fixa Longo Prazo".
    # Null for manual wallets, a bank/broker name for synced ones.
    institution_name: Optional[str] = None
    # Convenience rollup — filled by the service. Expressed in the group's
    # asset currencies without conversion; the frontend already handles
    # multi-currency totals.
    asset_count: int = 0
    current_value: float = 0.0
    current_value_primary: float = 0.0

    model_config = ConfigDict(from_attributes=True)
