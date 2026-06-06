import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class InstallmentCategoryInfo(BaseModel):
    id: uuid.UUID
    name: str
    icon: str
    color: str

    model_config = ConfigDict(from_attributes=True)


class InstallmentItem(BaseModel):
    number: int
    due_date: _Date
    amount: float
    status: str  # "PAID" | "PENDING"
    transaction_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class InstallmentTimelineItem(BaseModel):
    number: int
    due_date: _Date
    is_paid: bool
    paid_date: Optional[_Date] = None
    amount: float

    model_config = ConfigDict(from_attributes=True)


class InstallmentSummary(BaseModel):
    active_purchases_count: int
    total_estimated_amount: float
    total_paid_amount: float
    total_remaining_amount: float
    overall_progress_percentage: float
    final_maturity_date: Optional[_Date] = None

    model_config = ConfigDict(from_attributes=True)


class InstallmentPurchaseRead(BaseModel):
    id: uuid.UUID
    merchant_name: str
    status: str  # "ACTIVE" | "FINISHED"
    current_installment: int
    paid_count: int
    total_installments: int
    installment_monthly_amount: float
    institution_name: str
    total_amount: float
    paid_amount: float
    remaining_amount: float
    progress_percentage: float
    purchase_date: _Date
    final_due_date: Optional[_Date] = None
    is_manual: bool = False
    category: Optional[InstallmentCategoryInfo] = None
    next_due_date: Optional[_Date] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    total_amount_estimated: bool = False
    rounding_delta: float = 0.0
    has_partial_sync_data: bool = False
    installments: list[InstallmentItem] = []

    model_config = ConfigDict(from_attributes=True)


class InstallmentPurchaseDetail(BaseModel):
    id: uuid.UUID
    merchant_name: str
    purchase_date: _Date
    total_amount: float
    total_installments: int
    institution_name: str
    is_manual: bool = False
    account_id: uuid.UUID
    metrics: "InstallmentMetrics"
    installments_timeline: list[InstallmentItem]

    model_config = ConfigDict(from_attributes=True)


class InstallmentMetrics(BaseModel):
    paid_amount: float
    remaining_amount: float
    last_installment_date: Optional[_Date] = None

    model_config = ConfigDict(from_attributes=True)


class ManualInstallmentCreate(BaseModel):
    merchant_name: str
    account_id: uuid.UUID
    total_amount: Decimal
    total_installments: int = Field(ge=2, le=60)
    purchase_date: _Date
    monthly_amount: Optional[Decimal] = None
    category_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class ManualInstallmentUpdate(BaseModel):
    merchant_name: Optional[str] = None
    total_amount: Optional[Decimal] = None
    total_installments: Optional[int] = Field(default=None, ge=2, le=60)
    monthly_amount: Optional[Decimal] = None
    purchase_date: Optional[_Date] = None
    category_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class MarkInstallmentPaid(BaseModel):
    installment_number: int
    amount: Decimal
    date: _Date
