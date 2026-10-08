import uuid
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CadenceEnum(str, Enum):
    WEEKLY = "weekly"
    BI_WEEKLY = "bi_weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


class SubscriptionStatusEnum(str, Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    PENDING = "pending"


class FlowDirectionEnum(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class DetectedRecurringItem(BaseModel):
    merchant_name: str
    normalized_key: str
    direction: FlowDirectionEnum
    cadence: CadenceEnum
    average_interval_days: float
    occurrence_count: int
    first_seen_date: date
    last_seen_date: date
    next_expected_date: date
    average_amount: Decimal
    currency: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    is_subscription: bool
    status: SubscriptionStatusEnum
    estimated_annual_impact: Decimal
    category_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    sample_transaction_ids: list[uuid.UUID] = Field(default_factory=list)


class RecurringDetectionResponse(BaseModel):
    workspace_id: uuid.UUID
    total_detected: int
    active_subscriptions_count: int
    total_monthly_subscription_cost: Decimal
    total_annual_subscription_cost: Decimal
    items: list[DetectedRecurringItem]


class DailyForecastPoint(BaseModel):
    date: date
    starting_balance: Decimal
    recurring_inflow: Decimal
    recurring_outflow: Decimal
    discretionary_burn: Decimal
    net_change: Decimal
    ending_balance: Decimal
    events: list[str] = Field(default_factory=list)


class ForecastSummary(BaseModel):
    horizon_days: int
    start_date: date
    end_date: date
    starting_liquid_balance: Decimal
    projected_ending_balance: Decimal
    lowest_projected_balance: Decimal
    lowest_balance_date: date
    total_projected_inflow: Decimal
    total_projected_outflow: Decimal
    total_projected_discretionary: Decimal
    net_cashflow: Decimal
    runway_days: Optional[int] = None
    has_shortfall_risk: bool
    first_shortfall_date: Optional[date] = None


class CashflowForecastResponse(BaseModel):
    workspace_id: uuid.UUID
    summary: ForecastSummary
    daily_trajectory: list[DailyForecastPoint]
