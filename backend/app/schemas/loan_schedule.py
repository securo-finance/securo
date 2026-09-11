import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Schedule Entry Schemas
class LoanScheduleEntryRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    workspace_id: uuid.UUID
    schedule_version: int
    emi_number: int
    due_date: date
    principal_component: float
    interest_component: float
    emi_amount: float
    opening_balance: float
    closing_balance: float
    payment_status: str
    actual_payment_date: Optional[date] = None
    actual_amount_paid: Optional[float] = None
    linked_transaction_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class LoanScheduleEntryUpdate(BaseModel):
    due_date: Optional[date] = None
    principal_component: Optional[Decimal] = None
    interest_component: Optional[Decimal] = None
    emi_amount: Optional[Decimal] = None
    payment_status: Optional[str] = None
    actual_payment_date: Optional[date] = None
    actual_amount_paid: Optional[Decimal] = None
    notes: Optional[str] = None


class MarkPaymentStatusRequest(BaseModel):
    payment_status: str = Field(..., pattern="^(paid|partial|missed|skipped)$")
    actual_payment_date: Optional[date] = None
    actual_amount_paid: Optional[Decimal] = None
    transaction_id: Optional[uuid.UUID] = None


# Schedule Summary
class LoanScheduleSummary(BaseModel):
    total_emis: int
    paid_count: int
    remaining_count: int
    total_principal_paid: float
    total_interest_paid: float
    total_remaining_principal: float
    total_remaining_interest: float


class LoanScheduleResponse(BaseModel):
    account_id: uuid.UUID
    current_version: int
    schedules: list[LoanScheduleEntryRead]
    summary: LoanScheduleSummary


# Bulk Operations
class BulkUpdateDatesRequest(BaseModel):
    shift_days: Optional[int] = None
    new_emi_day: Optional[int] = Field(None, ge=1, le=31)
    from_emi_number: Optional[int] = Field(None, ge=1)


class RegenerateScheduleRequest(BaseModel):
    from_emi_number: int = Field(..., ge=1)
    new_principal_balance: Decimal
    new_interest_rate: Optional[Decimal] = None
    new_tenure_months: Optional[int] = None
    new_emi_amount: Optional[Decimal] = None
    reason: str


# Prepayment Schemas
class PrepaymentCreate(BaseModel):
    prepayment_amount: Decimal = Field(..., gt=0)
    prepayment_date: date
    recalculation_method: str = Field(..., pattern="^(reduce_emi|reduce_tenure)$")
    transaction_id: Optional[uuid.UUID] = None
    create_transaction: bool = False


class PrepaymentRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    workspace_id: uuid.UUID
    transaction_id: Optional[uuid.UUID]
    prepayment_amount: float
    prepayment_date: date
    recalculation_method: str
    schedule_version_before: int
    schedule_version_after: int
    tenure_change_months: Optional[int]
    emi_change_amount: Optional[float]
    created_at: date | datetime

    model_config = ConfigDict(from_attributes=True)


class PrepaymentSimulateRequest(BaseModel):
    prepayment_amount: Decimal = Field(..., gt=0)
    prepayment_date: date


class PrepaymentOption(BaseModel):
    new_emi_amount: Optional[Decimal] = None
    emi_reduction: Optional[Decimal] = None
    new_tenure_months: Optional[int] = None
    months_saved: Optional[int] = None
    new_payoff_date: Optional[date] = None
    tenure_months: Optional[int] = None
    emi_amount: Optional[Decimal] = None
    total_interest_saved: Decimal
    sample_schedule: list[LoanScheduleEntryRead]


class PrepaymentSimulation(BaseModel):
    reduce_emi_option: PrepaymentOption
    reduce_tenure_option: PrepaymentOption


# Transaction Linking
class AutoLinkRequest(BaseModel):
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    date_tolerance_days: int = Field(5, ge=0, le=30)
    amount_tolerance_pct: Decimal = Field(Decimal("2.0"), ge=0, le=100)
    auto_approve: bool = False


class PotentialMatch(BaseModel):
    schedule_entry_id: uuid.UUID
    transaction_id: uuid.UUID
    due_date: date
    transaction_date: date
    emi_amount: float
    transaction_amount: float
    confidence: str  # exact, high, medium, low


class AutoLinkResponse(BaseModel):
    potential_matches: list[PotentialMatch]
    auto_linked_count: int
    requires_review_count: int


class LinkTransactionRequest(BaseModel):
    transaction_id: uuid.UUID
