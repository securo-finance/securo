import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from app.core.money import bounded_money
from app.schemas.transaction_category_line import (
    TransactionCategoryLineRead,
    TransactionCategoryLinesInput,
)

WeekendAdjustment = Literal["none", "previous_friday", "next_monday"]
RecurringFrequency = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]
DayOfMonth = Annotated[int, Field(ge=1, le=31)]
# Months never have fewer than eighteen business days; 23 is the most any
# month has, so a larger number could only ever mean "the last one".
BusinessDay = Annotated[int, Field(ge=1, le=23)]
GraceDays = Annotated[int, Field(ge=0, le=15)]
AmountTolerance = Annotated[Decimal, Field(ge=0, le=50)]
HolidayCalendar = Literal["BR"]

# Callers (the agent tools among them) say "expense" and "income" as often as
# "debit" and "credit". Rows stored with those words were left out of every
# balance and total, so they are translated rather than stored.
_TYPE_ALIASES = {"expense": "debit", "income": "credit"}


def _normalize_type(value):
    if isinstance(value, str):
        value = value.strip().lower()
        return _TYPE_ALIASES.get(value, value)
    return value


RecurringType = Annotated[Literal["debit", "credit"], BeforeValidator(_normalize_type)]


def _normalize_keywords(value):
    """Store keywords as one comma-separated string, blanks dropped."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(part) for part in value]
    else:
        parts = str(value).split(",")
    cleaned = [part.strip() for part in parts if part.strip()]
    return ", ".join(cleaned) or None


MatchKeywords = Annotated[
    Optional[Annotated[str, Field(max_length=500)]], BeforeValidator(_normalize_keywords)
]


class RecurringTransactionCreate(BaseModel):
    description: str
    amount: Decimal
    currency: str = "USD"
    type: RecurringType
    frequency: str  # weekly, biweekly, monthly, quarterly, semiannual, yearly
    weekend_adjustment: WeekendAdjustment = "none"
    day_of_month: Optional[DayOfMonth] = None
    business_day: Optional[BusinessDay] = None
    count_saturdays: bool = False
    holiday_calendar: Optional[HolidayCalendar] = None
    grace_days: GraceDays = 0
    match_keywords: MatchKeywords = None
    amount_tolerance_percent: AmountTolerance = Decimal("0")
    start_date: _Date
    end_date: Optional[_Date] = None
    account_id: uuid.UUID
    category_id: Optional[uuid.UUID] = None
    skip_first: bool = False  # Set true when first occurrence already created as a transaction
    auto_generate: bool = True  # Materialize occurrences; when false, wait for the real charge
    # The existing transaction this bill is made from. It is linked to the new
    # bill, so it stops offering "make recurring" and counts as the first
    # occurrence the series already has.
    source_transaction_id: Optional[uuid.UUID] = None
    # Split across categories; every occurrence starts with these parts.
    category_lines: Optional[TransactionCategoryLinesInput] = None

    @field_validator("amount")
    @classmethod
    def check_amount_bounds(cls, value: Decimal) -> Decimal:
        return bounded_money(value)


class RecurringTransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    type: Optional[RecurringType] = None
    frequency: Optional[RecurringFrequency] = None
    weekend_adjustment: Optional[WeekendAdjustment] = None
    day_of_month: Optional[DayOfMonth] = None
    business_day: Optional[BusinessDay] = None
    count_saturdays: Optional[bool] = None
    holiday_calendar: Optional[HolidayCalendar] = None
    grace_days: Optional[GraceDays] = None
    match_keywords: MatchKeywords = None
    amount_tolerance_percent: Optional[AmountTolerance] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    auto_generate: Optional[bool] = None
    category_lines: Optional[TransactionCategoryLinesInput] = None

    @field_validator("amount")
    @classmethod
    def check_amount_bounds(cls, value: Optional[Decimal]) -> Decimal:
        # The column is NOT NULL; left out, the amount is not changed.
        if value is None:
            raise ValueError("amount cannot be null")
        return bounded_money(value)


class RecurringTransactionRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    description: str
    amount: Decimal
    currency: str
    type: str
    frequency: str
    weekend_adjustment: WeekendAdjustment = "none"
    day_of_month: Optional[int] = None
    business_day: Optional[int] = None
    count_saturdays: bool = False
    holiday_calendar: Optional[str] = None
    grace_days: int = 0
    match_keywords: Optional[str] = None
    amount_tolerance_percent: float = 0
    start_date: _Date
    end_date: Optional[_Date] = None
    is_active: bool
    auto_generate: bool = True
    next_occurrence: _Date
    amount_primary: Optional[float] = None
    fx_rate_used: Optional[float] = None
    has_category_lines: bool = False
    category_lines: list[TransactionCategoryLineRead] = []

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def next_due_date(self) -> _Date:
        """The day the next occurrence is actually due.

        ``next_occurrence`` is the nominal pointer the schedule advances by;
        a weekend adjustment or a business-day rule moves the real day.
        """
        from app.services.recurring_transaction_service import adjust_weekend_date

        return adjust_weekend_date(
            self.next_occurrence,
            self.weekend_adjustment,
            business_day=self.business_day,
            count_saturdays=self.count_saturdays,
            holiday_calendar=self.holiday_calendar,
        )


class RecurringMonthSummary(BaseModel):
    """What the active recurring definitions add up to in one month, in the
    primary currency, counting each occurrence that falls in it."""

    month: _Date
    currency: str
    income: Decimal
    expenses: Decimal
    net: Decimal
    # Occurrences of transfer-like categories, kept out of the totals.
    transfer_count: int
    # Occurrences left out because no exchange rate could be found.
    unconverted_count: int
