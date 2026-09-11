import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CombinedSimEvent(BaseModel):
    type: str = Field(..., pattern="^(one_time_prepayment|recurring_prepayment|rate_change|emi_holiday)$")
    date: date
    amount: Optional[Decimal] = None
    new_rate: Optional[Decimal] = None
    months: Optional[int] = Field(None, ge=1, le=600)
    end_date: Optional[date] = None
    label: Optional[str] = None


class CombinedSimulationRequest(BaseModel):
    account_id: uuid.UUID
    events: list[CombinedSimEvent] = Field(default_factory=list)
    strategy: str = Field("reduce_tenure", pattern="^(reduce_tenure|reduce_emi)$")
    as_of_date: Optional[date] = None


class CommitmentCreate(BaseModel):
    account_id: uuid.UUID
    kind: str = Field(..., pattern="^(one_time_prepayment|recurring_prepayment)$")
    amount: Decimal = Field(..., gt=0)
    start_date: date
    end_date: Optional[date] = None
    day_of_month: Optional[int] = Field(None, ge=1, le=28)
    funding_account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class CommitmentRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    kind: str
    amount: float
    start_date: date
    end_date: Optional[date] = None
    day_of_month: Optional[int] = None
    status: str
    funding_account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    budget_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    transaction_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
