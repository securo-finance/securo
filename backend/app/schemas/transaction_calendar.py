import uuid
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TransactionCalendarItem(BaseModel):
    kind: Literal["actual", "projected"]
    id: Optional[uuid.UUID] = None
    recurring_id: Optional[uuid.UUID] = None
    date: date
    description: str
    amount: float
    amount_primary: Optional[float] = None
    currency: str
    type: Literal["debit", "credit"]
    account_id: Optional[uuid.UUID] = None
    account_name: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    category_name: Optional[str] = None
    category_icon: Optional[str] = None
    category_color: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    transfer_pair_id: Optional[uuid.UUID] = None
    is_transfer: bool = False
    is_ignored: bool = False
    #: Kept out of the day's income/expense totals but still moving the
    #: balance, so the row needs its own marker: without one the day card
    #: shows 0/0 while its ending balance jumps, and nothing explains why.
    exclude_from_pnl: bool = False


class TransactionCalendarDay(BaseModel):
    date: date
    in_month: bool
    ending_balance: float
    # Combined totals kept for backwards compatibility with existing consumers.
    income: float = 0.0
    expense: float = 0.0
    transfer_net: float = 0.0
    actual_income: float = 0.0
    actual_expense: float = 0.0
    actual_transfer_net: float = 0.0
    projected_income: float = 0.0
    projected_expense: float = 0.0
    projected_transfer_net: float = 0.0
    actual_count: int = 0
    projected_count: int = 0
    has_income: bool = False
    has_expense: bool = False
    has_transfer: bool = False
    items: list[TransactionCalendarItem] = Field(default_factory=list)


class TransactionCalendarResponse(BaseModel):
    month: str
    currency: str
    account_ids: list[uuid.UUID] | None = None
    days: list[TransactionCalendarDay]
