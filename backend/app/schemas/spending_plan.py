import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SpendingPlanWrite(BaseModel):
    daily_allowance: Decimal = Field(ge=0)
    balance_floor: Decimal = Decimal("0")
    account_ids: Optional[list[uuid.UUID]] = None
    horizon_months: int = Field(default=12, ge=1, le=36)


class SpendingPlanRead(BaseModel):
    daily_allowance: Decimal
    balance_floor: Decimal
    account_ids: Optional[list[uuid.UUID]] = None
    horizon_months: int

    model_config = ConfigDict(from_attributes=True)


class SimulatedPurchase(BaseModel):
    description: Optional[str] = None
    amount: Decimal = Field(gt=0)
    installments: int = Field(default=1, ge=1, le=120)
    first_date: _Date


class SpendingPlanSimulation(BaseModel):
    daily_allowance: Optional[Decimal] = Field(default=None, ge=0)
    purchases: list[SimulatedPurchase] = Field(max_length=20)


class SpendingPlanPoint(BaseModel):
    date: _Date
    balance: float


class SpendingPlanEvent(BaseModel):
    #: The day it moves the balance. A card charge sits on its bill's due date.
    date: _Date
    #: None for "other": the part of a day's move no row names, such as
    #: today's posted rows.
    description: Optional[str] = None
    #: Signed, in the primary currency.
    amount: float
    source: Literal["recurring", "planned", "card", "other", "scenario"]
    account_name: Optional[str] = None


class SpendingPlanToday(BaseModel):
    allowance: float
    spent: float
    remaining: float


class SpendingPlanTimeline(BaseModel):
    currency: str
    daily_allowance: float
    balance_floor: float
    horizon_end: _Date
    #: Largest daily allowance that keeps every day through the horizon at or
    #: above the floor.
    safe_daily_allowance: float
    #: How much the balance grows per day on average through the horizon when
    #: nothing beyond bills and planned rows is spent: what is left to spend.
    average_daily_surplus: float
    #: Last day the balance stays at or above the floor. None when today is
    #: already below it.
    covered_until: Optional[_Date] = None
    covered_through_horizon: bool
    #: Balance at the close of yesterday, before today's moves.
    opening_balance: float
    #: The day the balance comes closest to the floor per day ahead of it: the
    #: safe allowance is what fits from today through this day.
    binding_date: Optional[_Date] = None
    today: SpendingPlanToday
    points: list[SpendingPlanPoint]
    #: Every move behind the points. The events on a day sum to that day's
    #: change in balance before the allowance.
    events: list[SpendingPlanEvent]
