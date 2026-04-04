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


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    balance: Optional[Decimal] = None
    balance_date: Optional[date] = None


class AccountRead(AccountBase):
    id: uuid.UUID
    user_id: uuid.UUID
    monthly_period_id: Optional[uuid.UUID] = None
    connection_id: Optional[uuid.UUID] = None
    external_id: Optional[str] = None
    current_balance: float = 0.0
    previous_balance: Optional[float] = None
    balance_primary: Optional[float] = None
    is_closed: bool = False
    closed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AccountSummary(BaseModel):
    account_id: uuid.UUID
    current_balance: float
    monthly_income: float
    monthly_expenses: float
    current_balance_primary: Optional[float] = None
    monthly_income_primary: Optional[float] = None
    monthly_expenses_primary: Optional[float] = None
