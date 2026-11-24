import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

from app.core.money import MAX_MONEY_AMOUNT

# A budget limit is money in the same Numeric(15, 2) column as a transaction
# amount, so it keeps the same ceiling (ADR 0008), and 0.01 is the smallest
# limit that can be spent against. A limit out of that range used to reach the
# driver, where the answer carried its internals instead of naming the bound.
MIN_BUDGET_AMOUNT = Decimal("0.01")
MAX_BUDGET_AMOUNT = MAX_MONEY_AMOUNT


def validate_budget_amount(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    """Check a limit against what Numeric(15, 2) stores.

    `allow_zero` is for an edit: the budgets screen clears a limit by sending
    `amount: 0`, and that zero row is the tombstone that stops a recurring
    series (`planBudgetChange` in budget-settings-utils.ts). Create and batch
    keep the 0.01 floor, since a new budget of nothing has no use.

    The ceiling is the exact last value the column holds, so a limit that
    would round up to the next digit (9999999999999.999) is refused here
    instead of overflowing the write.
    """
    if allow_zero:
        if value < 0:
            raise PydanticCustomError(
                "budget_amount_negative",
                "The budget limit cannot be negative",
            )
    elif value < MIN_BUDGET_AMOUNT:
        raise PydanticCustomError(
            "budget_amount_not_positive",
            "The budget limit must be at least 0.01",
        )
    if value > MAX_BUDGET_AMOUNT:
        raise PydanticCustomError(
            "budget_amount_too_large",
            "The budget limit is too large",
        )
    return value


def _validate_budget_amount(value: Decimal) -> Decimal:
    return validate_budget_amount(value)


def _validate_budget_update_amount(value: Decimal) -> Decimal:
    return validate_budget_amount(value, allow_zero=True)


BudgetAmount = Annotated[Decimal, AfterValidator(_validate_budget_amount)]
BudgetUpdateAmount = Annotated[Decimal, AfterValidator(_validate_budget_update_amount)]


class BudgetCreate(BaseModel):
    category_id: uuid.UUID
    amount: BudgetAmount
    month: _Date  # First day of month
    is_recurring: bool = False


class BudgetUpdate(BaseModel):
    amount: Optional[BudgetUpdateAmount] = None
    effective_month: Optional[_Date] = None


class BudgetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    # The currency `amount` was typed in; `amount_primary` is the same limit
    # in the viewer's display currency.
    currency: Optional[str] = None
    amount_primary: Optional[Decimal] = None
    month: _Date
    is_recurring: bool

    model_config = ConfigDict(from_attributes=True)


class BudgetVsActual(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    budget_amount: Optional[Decimal] = None
    actual_amount: Decimal
    projected_amount: Decimal = Decimal("0")
    prev_month_amount: Decimal = Decimal("0")
    projected_prev_month_amount: Decimal = Decimal("0")
    percentage_used: Optional[float] = None
    is_recurring: bool = False


class BudgetSuggestion(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    suggested_amount: Decimal
    total_spent: Decimal
    months_with_data: int
    existing_budget: bool = False


class BudgetSuggestionResponse(BaseModel):
    month: _Date
    months_requested: int
    months_analyzed: int
    suggestions: list[BudgetSuggestion]


class BudgetBatchItem(BaseModel):
    category_id: uuid.UUID
    amount: BudgetAmount


class BudgetBatchCreate(BaseModel):
    month: _Date
    is_recurring: bool = False
    items: list[BudgetBatchItem] = Field(min_length=1, max_length=200)


class BudgetCopyMonthRequest(BaseModel):
    source_month: _Date
    target_month: _Date
    adjustment_percentage: Decimal = Field(
        default=Decimal("0.0"), ge=Decimal("-100"), le=Decimal("1000")
    )
    overwrite_existing: bool = False


class BudgetRolloverCategory(BaseModel):
    category_id: uuid.UUID
    category_name: str
    budgeted: Decimal
    actual_spent: Decimal
    projected_spent: Decimal
    remaining: Decimal
    carryover_eligible: bool


class BudgetRolloverSummaryResponse(BaseModel):
    month: _Date
    total_budgeted: Decimal
    total_spent: Decimal
    total_projected_spent: Decimal
    net_surplus: Decimal
    categories: list[BudgetRolloverCategory]


class BudgetMultiMonthForecastItem(BaseModel):
    month: _Date
    projected_budget: Decimal
    projected_spend: Decimal
    projected_variance: Decimal


class BudgetForecastResponse(BaseModel):
    forecast_months: int
    items: list[BudgetMultiMonthForecastItem]
