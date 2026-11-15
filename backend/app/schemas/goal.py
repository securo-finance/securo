import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SalaryMonths = Literal[3, 6, 12]


def _check_salary_pair(recurring_id: Optional[uuid.UUID], months: Optional[int]) -> None:
    if (recurring_id is None) != (months is None):
        raise ValueError("salary_recurring_id and salary_months must both be set or both be null")


class GoalCreate(BaseModel):
    name: str
    # Ignored for salary goals: the service resolves it from the salary.
    target_amount: Optional[Decimal] = None
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
    salary_recurring_id: Optional[uuid.UUID] = None
    salary_months: Optional[SalaryMonths] = None

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: str) -> str:
        if v not in ("manual", "account", "asset", "asset_group", "net_worth"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, or net_worth")
        return v

    @model_validator(mode="after")
    def validate_target(self) -> "GoalCreate":
        _check_salary_pair(self.salary_recurring_id, self.salary_months)
        if self.salary_recurring_id is None and self.target_amount is None:
            raise ValueError("target_amount is required unless the goal targets a salary multiple")
        return self


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
    salary_recurring_id: Optional[uuid.UUID] = None
    salary_months: Optional[SalaryMonths] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("active", "completed", "paused", "archived"):
            raise ValueError("status must be active, completed, paused, or archived")
        return v

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("manual", "account", "asset", "asset_group", "net_worth"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, or net_worth")
        return v

    @model_validator(mode="after")
    def validate_salary_pair(self) -> "GoalUpdate":
        sent = {"salary_recurring_id", "salary_months"} & self.model_fields_set
        if sent and len(sent) != 2:
            raise ValueError("salary_recurring_id and salary_months must be sent together")
        _check_salary_pair(self.salary_recurring_id, self.salary_months)
        return self


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

    salary_recurring_id: Optional[uuid.UUID] = None
    salary_months: Optional[int] = None
    salary_name: Optional[str] = None
    # Resolved from the linked recurring, in the goal currency.
    monthly_salary: Optional[Decimal] = None

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
    salary_months: Optional[int] = None
    monthly_salary: Optional[Decimal] = None
