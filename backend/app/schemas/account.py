import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

LoanKind = Literal["mortgage", "auto", "personal", "student", "other"]
LoanRatePeriod = Literal["monthly", "yearly_effective", "yearly_nominal"]
LoanAmortization = Literal["price", "sac"]


class AccountBase(BaseModel):
    name: str
    type: str
    balance: Decimal
    currency: str = "USD"
    # Loan accounts only: the property or vehicle the loan is secured against.
    secured_asset_id: Optional[uuid.UUID] = None


class AccountCreate(BaseModel):
    name: str
    type: str
    balance: Decimal = Decimal("0.00")
    balance_date: Optional[date] = None
    currency: str = "USD"
    secured_asset_id: Optional[uuid.UUID] = None
    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None
    # Loan accounts only: contract terms, all optional (see loan_schedule.py).
    loan_kind: Optional[LoanKind] = None
    loan_original_amount: Optional[Decimal] = Field(default=None, gt=0)
    loan_interest_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    loan_rate_period: Optional[LoanRatePeriod] = None
    loan_term_months: Optional[int] = Field(default=None, ge=1, le=600)
    loan_first_payment_date: Optional[date] = None
    loan_amortization: Optional[LoanAmortization] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[str] = None
    secured_asset_id: Optional[uuid.UUID] = None
    balance: Optional[Decimal] = None
    balance_date: Optional[date] = None
    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None
    # Loan accounts only: contract terms, all optional (see loan_schedule.py).
    loan_kind: Optional[LoanKind] = None
    loan_original_amount: Optional[Decimal] = Field(default=None, gt=0)
    loan_interest_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    loan_rate_period: Optional[LoanRatePeriod] = None
    loan_term_months: Optional[int] = Field(default=None, ge=1, le=600)
    loan_first_payment_date: Optional[date] = None
    loan_amortization: Optional[LoanAmortization] = None


class AccountRead(AccountBase):
    id: uuid.UUID
    user_id: uuid.UUID
    connection_id: Optional[uuid.UUID] = None
    external_id: Optional[str] = None
    display_name: Optional[str] = None
    # Last 4 chars of the bank's identifier, when the provider exposes one.
    # Read-only: absent from AccountUpdate because sync owns it.
    masked_number: Optional[str] = None
    # The account's own institution (issue #345), falling back to the linked
    # BankConnection's when the provider doesn't distinguish. Null for manual
    # accounts.
    institution_name: Optional[str] = None
    institution_logo_url: Optional[str] = None
    current_balance: float = 0.0
    previous_balance: Optional[float] = None
    # Date of the opening-balance transaction; null when there is none.
    balance_date: Optional[date] = None
    balance_primary: Optional[float] = None
    # True when the account is in a foreign currency and no real FX rate is
    # available, so balance_primary is left unset rather than a 1:1 guess.
    fx_fallback: bool = False
    credit_limit: Optional[float] = None
    available_credit: Optional[float] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    next_close_date: Optional[date] = None
    next_due_date: Optional[date] = None
    minimum_payment: Optional[float] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None
    loan_kind: Optional[str] = None
    loan_original_amount: Optional[float] = None
    loan_interest_rate: Optional[float] = None
    loan_rate_period: Optional[str] = None
    loan_term_months: Optional[int] = None
    loan_first_payment_date: Optional[date] = None
    loan_amortization: Optional[str] = None
    shared_balance_group: Optional[str] = None
    # Open bill of a card on a shared credit line (earliest due_date on or
    # after today), in the account's currency. The sidebar leads with it so
    # every card stops repeating the contract-wide balance. Null when the
    # card has no such bill, and for all accounts outside a shared line.
    open_bill_total: Optional[float] = None
    # Same bill converted to the user's primary currency. None when the
    # account is already in it or no real FX rate is available.
    open_bill_total_primary: Optional[float] = None
    is_closed: bool = False
    closed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CreditCardBillRead(BaseModel):
    """Provider-agnostic credit-card bill (fatura) — issue #92.

    Provider-specific extras live in `raw_data` on the model but are not
    exposed here so that consumers don't form provider-shaped dependencies.
    """

    id: uuid.UUID
    account_id: uuid.UUID
    external_id: str
    due_date: date
    total_amount: float
    currency: str
    minimum_payment: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class AccountSummary(BaseModel):
    account_id: uuid.UUID
    current_balance: float
    opening_balance: float
    monthly_income: float
    monthly_expenses: float
    projected_income: float = 0.0
    projected_expenses: float = 0.0
    current_balance_primary: Optional[float] = None
    opening_balance_primary: Optional[float] = None
    monthly_income_primary: Optional[float] = None
    monthly_expenses_primary: Optional[float] = None
    projected_income_primary: Optional[float] = None
    projected_expenses_primary: Optional[float] = None
    # True when no real FX rate is available, so the *_primary fields are
    # left unset rather than a 1:1 guess.
    fx_fallback: bool = False


class AccountRunningBalancePoint(BaseModel):
    """End-of-day balance on the account page's walk (#1202)."""

    date: date
    balance: float
    balance_primary: Optional[float] = None


class LoanInstallmentRead(BaseModel):
    number: int
    due_date: date
    payment: float
    interest: float
    principal: float
    balance: float


class LoanScheduleRead(BaseModel):
    """A loan's payment schedule, computed from its contract terms on read."""

    account_id: uuid.UUID
    currency: str
    # What the account's own transactions say is still owed (never negative).
    current_debt: float
    # False until every term the schedule needs is set; `missing` lists them.
    configured: bool
    missing: list[str] = []
    monthly_payment: Optional[float] = None
    payments_due: Optional[int] = None
    remaining_payments: Optional[int] = None
    next_payment: Optional[LoanInstallmentRead] = None
    scheduled_balance: Optional[float] = None
    # Positive when less is owed than the contract expects by now.
    ahead_by: Optional[float] = None
    total_interest: Optional[float] = None
    total_paid: Optional[float] = None
    payoff_date: Optional[date] = None
    projected_payoff_date: Optional[date] = None
    paid_off: bool = False
    installments: list[LoanInstallmentRead] = []


class LoanBalanceUpdate(BaseModel):
    """What the lender says is owed on a date, to bring the ledger in line."""

    balance: Decimal = Field(ge=0)
    date: date
    description: str = Field(min_length=1, max_length=500)
    # Category of the adjustment when the debt grew (interest, indexation).
    category_id: Optional[uuid.UUID] = None


class LoanBalanceAdjustment(BaseModel):
    # Positive when the debt grew, negative when it fell, zero when the
    # ledger already matched and nothing was recorded.
    adjustment: float
    current_debt: float
    transaction_id: Optional[uuid.UUID] = None
