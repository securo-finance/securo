from typing import Optional

from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    total_balance: dict[str, float]  # currency -> amount
    total_balance_primary: float = 0.0  # consolidated in primary currency
    projected_balance: dict[str, float] = Field(default_factory=dict)  # current balance plus forecast rows
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


class BalanceHistory(BaseModel):
    current: list[DailyBalance]
    previous: list[DailyBalance]


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
