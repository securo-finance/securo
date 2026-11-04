import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.invoice import InvoiceLineInput, InvoicePayee

#: Decisions a human took about an agreement. Never PATCHed as a string:
#: each changes through the action that causes it (`/pause`, `/end`).
ScheduleStatus = Literal["active", "paused", "ended"]
ScheduleFrequency = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]
ScheduleEndType = Literal["never", "on_date", "after_count"]
ScheduleEndReason = Literal["canceled_by_client", "canceled_by_us", "completed", "unpaid", "other"]
SchedulePauseReason = Literal["manual", "failures"]
ScheduleOrigin = Literal["local", "imported"]


class ScheduleTermInput(BaseModel):
    effective_from: _Date
    lines: list[InvoiceLineInput] = Field(..., min_length=1)
    discount: Optional[Decimal] = Field(default=None, ge=0)


class ScheduleTermUpdate(BaseModel):
    effective_from: Optional[_Date] = None
    lines: Optional[list[InvoiceLineInput]] = Field(default=None, min_length=1)
    discount: Optional[Decimal] = Field(default=None, ge=0)


class ScheduleTermRead(BaseModel):
    id: uuid.UUID
    effective_from: _Date
    lines: list[dict[str, Any]]
    discount: Decimal
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    payee_id: Optional[uuid.UUID] = None
    frequency: ScheduleFrequency
    #: Defaults to today. The anchor: every period is counted from it.
    start_date: Optional[_Date] = None
    end_type: ScheduleEndType = "never"
    end_date: Optional[_Date] = None
    end_count: Optional[int] = Field(default=None, ge=1)
    #: Null means the workspace default at each generation.
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    notes: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None
    #: The first term, in force from `start_date`.
    lines: list[InvoiceLineInput] = Field(..., min_length=1)
    discount: Optional[Decimal] = Field(default=None, ge=0)
    origin: Optional[ScheduleOrigin] = None
    external_source: Optional[str] = Field(default=None, max_length=50)
    external_id: Optional[str] = Field(default=None, max_length=255)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    payee_id: Optional[uuid.UUID] = None
    #: Fixed once an invoice exists under the agreement.
    frequency: Optional[ScheduleFrequency] = None
    start_date: Optional[_Date] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    end_type: Optional[ScheduleEndType] = None
    end_date: Optional[_Date] = None
    end_count: Optional[int] = Field(default=None, ge=1)
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None


class ScheduleEnd(BaseModel):
    reason: ScheduleEndReason = "other"
    #: Defaults to today.
    ended_at: Optional[_Date] = None


class ScheduleLinkInvoice(BaseModel):
    invoice_id: uuid.UUID
    #: Must be the first day of one of this agreement's periods.
    period_start: _Date


class MakeRecurring(BaseModel):
    """Turn an issued invoice into period one of a new agreement."""

    frequency: ScheduleFrequency
    #: Defaults to the invoice's own date. Earlier makes the invoice a
    #: later period, so the ones before it can be linked as history.
    start_date: Optional[_Date] = None
    #: Defaults to the first line's description, then the client's name.
    name: Optional[str] = Field(default=None, max_length=200)
    end_type: ScheduleEndType = "never"
    end_date: Optional[_Date] = None
    end_count: Optional[int] = Field(default=None, ge=1)
    #: Defaults to the invoice's own issue-to-due gap.
    payment_terms_days: Optional[int] = Field(default=None, ge=0)


class ScheduleRead(BaseModel):
    id: uuid.UUID
    name: str
    payee_id: Optional[uuid.UUID]
    payee: Optional[InvoicePayee] = None
    origin: str
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    status: ScheduleStatus
    pause_reason: Optional[SchedulePauseReason] = None
    ended_at: Optional[_Date] = None
    end_reason: Optional[ScheduleEndReason] = None
    frequency: ScheduleFrequency
    start_date: _Date
    end_type: ScheduleEndType
    end_date: Optional[_Date] = None
    end_count: Optional[int] = None
    payment_terms_days: Optional[int] = None
    currency: str
    notes: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None
    next_sequence: int
    last_generated_at: Optional[datetime] = None
    consecutive_failures: int
    terms: list[ScheduleTermRead] = []
    created_at: datetime

    #: The derived answers. `api/invoice_schedules.py::_serialize` is the
    #: sole constructor and fills every one; the defaults only let the
    #: ORM row validate first. None of them is ever stored.
    current_term: Optional[ScheduleTermRead] = None
    #: The term the next emitted period will be priced by, which differs
    #: from `current_term` only when a raise is recorded ahead.
    next_term: Optional[ScheduleTermRead] = None
    monthly_amount: Decimal = Decimal("0")
    next_period_start: Optional[_Date] = None
    invoice_count: int = 0
    amount_invoiced: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    past_due_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ScheduleCurrencySummary(BaseModel):
    currency: str
    monthly_recurring: Decimal
    active_count: int
    ended_recently_count: int
    monthly_lost: Decimal
    past_due_count: int


class ScheduleSummaryRead(BaseModel):
    active_count: int
    paused_count: int
    ended_count: int
    by_currency: list[ScheduleCurrencySummary]


class SchedulePeriodRead(BaseModel):
    """One period as a label: for the picker that asks "which period is
    this invoice for"."""

    sequence: int
    period_start: _Date
    period_end: _Date
    #: Whether an invoice already answers for it.
    taken: bool
