import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AccountBase(BaseModel):
    name: str
    type: str
    balance: Decimal
    currency: str = "USD"


class AccountCreate(BaseModel):
    name: str
    type: str
    balance: Decimal = Decimal("0.00")
    balance_date: Optional[date] = None
    currency: str = "USD"
    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[Decimal] = None
    balance_date: Optional[date] = None
    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None


class AccountRead(AccountBase):
    id: uuid.UUID
    user_id: uuid.UUID
    connection_id: Optional[uuid.UUID] = None
    external_id: Optional[str] = None
    display_name: Optional[str] = None
    # Last 4 chars of the bank's identifier, when the provider exposes one.
    # Read-only: absent from AccountUpdate because sync owns it.
    masked_number: Optional[str] = None
    # Denormalized from the linked BankConnection so every surface that shows
    # an account (transactions list, accounts page, dashboard) can render the
    # bank identity without a separate join. Null for manual accounts.
    institution_name: Optional[str] = None
    institution_logo_url: Optional[str] = None
    current_balance: float = 0.0
    previous_balance: Optional[float] = None
    balance_primary: Optional[float] = None
    credit_limit: Optional[float] = None
    available_credit: Optional[float] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    next_close_date: Optional[date] = None
    next_due_date: Optional[date] = None
    minimum_payment: Optional[float] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None
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
    monthly_income: float
    monthly_expenses: float
    current_balance_primary: Optional[float] = None
    monthly_income_primary: Optional[float] = None
    monthly_expenses_primary: Optional[float] = None
