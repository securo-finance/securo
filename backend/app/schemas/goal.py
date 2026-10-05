import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GoalAllocationInput(BaseModel):
    goal_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class GoalAllocationRead(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    goal_name: str
    transaction_id: Optional[uuid.UUID] = None
    amount: Decimal
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GoalAdjustmentCreate(BaseModel):
    amount: Decimal = Field(max_digits=15, decimal_places=2)

    @field_validator("amount")
    @classmethod
    def validate_nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("amount must not be zero")
        return value


class GoalCreate(BaseModel):
    name: str
    target_amount: Decimal
    current_amount: Decimal = Decimal("0")
    currency: str = "USD"
    target_date: Optional[_Date] = None
    tracking_type: str = "manual"
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    metadata_json: Optional[Any] = None
    initial_allocation: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=15, decimal_places=2
    )

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: str) -> str:
        if v not in ("manual", "account", "asset", "asset_group", "net_worth", "pocket"):
            raise ValueError(
                "tracking_type must be manual, account, asset, asset_group, net_worth, or pocket"
            )
        return v


class GoalUpdate(BaseModel):
    name: Optional[str] = None
    target_amount: Optional[Decimal] = None
    current_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    target_date: Optional[_Date] = None
    tracking_type: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None
    metadata_json: Optional[Any] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("active", "completed", "paused", "archived"):
            raise ValueError("status must be active, completed, paused, or archived")
        return v

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("manual", "account", "asset", "asset_group", "net_worth", "pocket"):
            raise ValueError(
                "tracking_type must be manual, account, asset, asset_group, net_worth, or pocket"
            )
        return v


class GoalRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    currency: str
    target_amount_primary: Optional[Decimal] = None
    current_amount_primary: Optional[Decimal] = None
    target_date: Optional[_Date] = None
    tracking_type: str
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    position: int
    metadata_json: Optional[Any] = None
    created_at: datetime
    updated_at: datetime

    # Computed fields
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None  # ahead, on_track, behind, overdue, achieved

    # Linked account/asset info
    account_name: Optional[str] = None
    asset_name: Optional[str] = None
    asset_group_name: Optional[str] = None

    # Pocket-only account breakdown. These remain null for every existing
    # tracking type so the API is additive and old clients keep their meaning.
    account_balance: Optional[Decimal] = None
    account_reserved_total: Optional[Decimal] = None
    account_available: Optional[Decimal] = None
    is_underfunded: bool = False

    model_config = ConfigDict(from_attributes=True)


class GoalSummary(BaseModel):
    id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    currency: str
    target_date: Optional[_Date] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None
