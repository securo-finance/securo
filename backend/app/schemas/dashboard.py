import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


class DashboardSummary(BaseModel):
    total_balance: dict[str, float]  # currency -> amount
    total_balance_primary: float = 0.0  # consolidated in primary currency
    projected_balance: dict[str, float] = Field(
        default_factory=dict
    )  # current balance plus forecast rows
    projected_balance_primary: float = 0.0  # consolidated projected balance
    balance_date: str  # ISO date string, e.g. "2026-03-02"
    monthly_income: float
    monthly_expenses: float
    monthly_income_primary: float = 0.0
    monthly_expenses_primary: float = 0.0
    projected_income: float = 0.0
    projected_expenses: float = 0.0
    projected_income_primary: float = 0.0
    projected_expenses_primary: float = 0.0
    accounts_count: int
    pending_categorization: int
    pending_categorization_amount: float
    assets_value: dict[str, float] = Field(default_factory=dict)  # currency -> total asset value
    assets_value_primary: float = 0.0
    primary_currency: str = "USD"
    # No real FX rate for at least one currency bucket: the *_primary totals
    # sum that bucket at 1:1, so they are an estimate, not a conversion.
    # Same flag the account list carries (issue #1165).
    fx_fallback: bool = False
    # Net pending balance from group splits (in primary currency).
    # Negative = the user is a net debtor (others paid for them, debt
    # owed). Positive = the user is a net creditor (paid for others,
    # waiting to be paid back). Computed from group balance lines so
    # it already accounts for any partial settlements.
    pending_shares_net: float = 0.0


class SpendingByCategory(BaseModel):
    category_id: Optional[str]
    category_name: str
    category_icon: str
    category_color: str
    # Posted-only, so the breakdown sums to the expenses card.
    total: float
    # Actual plus forecast, mirroring the card's projected sub-line.
    projected_total: float = 0.0
    percentage: float


class MonthlyTrend(BaseModel):
    month: str  # "2026-01"
    income: float
    expenses: float


class DailyBalance(BaseModel):
    day: int
    balance: Optional[float]  # None for future days beyond cutoff


class DailyBaseline(BaseModel):
    day: int
    median: Optional[float] = None  # None when fewer than 2 months contribute
    lower_quartile: Optional[float] = None  # None when fewer than 3 contribute
    upper_quartile: Optional[float] = None  # None when fewer than 3 contribute


class BalanceHistory(BaseModel):
    current: list[DailyBalance]
    opening_balance: float
    baseline: list[DailyBaseline]  # cumulative change since month opening
    comparison: Optional[list[DailyBalance]] = None
    comparison_month: Optional[str] = None
    comparison_opening_balance: Optional[float] = None


class ProjectedTransaction(BaseModel):
    recurring_id: str
    account_id: Optional[str] = None
    description: str
    amount: float
    amount_primary: Optional[float] = None
    currency: str
    type: str  # debit, credit
    date: str  # YYYY-MM-DD
    category_id: Optional[str]
    category_name: Optional[str]
    category_icon: Optional[str]
    category_color: Optional[str] = None
    category_treat_as_transfer: bool = False


# Server-owned v1 registry; mirrors components/dashboard/widgets.ts.
DASHBOARD_WIDGET_IDS = frozenset({
    "hero-balance", "uncategorized-banner", "spending-charts",
    "balance-evolution", "goals-progress", "period-transactions",
})


class DashboardLayoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    visible: StrictBool
    order: StrictInt = Field(ge=0)

    @field_validator("id")
    @classmethod
    def registered_id(cls, value: str) -> str:
        if value not in DASHBOARD_WIDGET_IDS:
            raise ValueError("Unknown dashboard widget")
        return value


class DashboardLayoutWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: StrictInt
    workspaceId: str
    items: list[DashboardLayoutItem] = Field(min_length=1, max_length=64)

    @field_validator("version")
    @classmethod
    def v1_only(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Only dashboard layout version 1 is supported")
        return value

    @field_validator("workspaceId")
    @classmethod
    def canonical_workspace(cls, value: str) -> str:
        if str(uuid.UUID(value)) != value:
            raise ValueError("workspaceId must be a canonical UUID")
        return value

    @model_validator(mode="after")
    def valid_items(self) -> "DashboardLayoutWrite":
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate dashboard widgets")
        if sorted(item.order for item in self.items) != list(range(len(self.items))):
            raise ValueError("Widget order must be contiguous")
        if not any(item.visible for item in self.items):
            raise ValueError("At least one widget must be visible")
        return self


class CommittedDashboardLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspaceId: str
    layout: DashboardLayoutWrite
