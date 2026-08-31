import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DebtInstallmentRead(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    installment_number: int
    due_date: _Date
    amount: Decimal
    principal_portion: Decimal
    interest_portion: Decimal
    status: str
    paid_date: Optional[_Date] = None
    paid_amount: Optional[Decimal] = None
    transaction_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class DebtPlanBase(BaseModel):
    kind: str
    collection_mode: str = "manual"
    interest_rate: Decimal = Field(ge=0, default=Decimal("0"))
    installment_amount: Decimal = Field(gt=0)
    num_installments: int = Field(gt=0, le=360)
    first_due_date: _Date
    frequency: str = "monthly"
    notes: Optional[str] = None


class DebtPlanCreate(DebtPlanBase):
    # Immediately makes this the active plan (superseding any current
    # active plan) instead of leaving it as a `proposed` simulation.
    activate: bool = False


class DebtPlanRead(DebtPlanBase):
    id: uuid.UUID
    debt_id: uuid.UUID
    workspace_id: uuid.UUID
    status: str
    created_at: datetime
    installments: list[DebtInstallmentRead] = []

    model_config = ConfigDict(from_attributes=True)


class DebtBase(BaseModel):
    kind: str
    creditor_name: str
    contract_reference: Optional[str] = None
    notes: Optional[str] = None
    original_principal: Decimal = Field(gt=0)
    current_balance: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, default="BRL")
    related_account_id: Optional[uuid.UUID] = None
    status: str = "active"
    opened_date: _Date


class DebtCreate(DebtBase):
    pass


class DebtUpdate(BaseModel):
    kind: Optional[str] = None
    creditor_name: Optional[str] = None
    contract_reference: Optional[str] = None
    notes: Optional[str] = None
    current_balance: Optional[Decimal] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    related_account_id: Optional[uuid.UUID] = None
    status: Optional[str] = None


class DebtRead(DebtBase):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_at: datetime
    plans: list[DebtPlanRead] = []

    model_config = ConfigDict(from_attributes=True)


class DebtInstallmentPay(BaseModel):
    paid_date: _Date
    paid_amount: Optional[Decimal] = Field(default=None, gt=0)
    # Only meaningful for a `manual` plan — ignored (must be omitted) for
    # payroll_deduction installments, which never have a real transaction.
    transaction_id: Optional[uuid.UUID] = None


class DebtStrategySettingRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    method: str
    extra_monthly_amount: Decimal
    model_config = ConfigDict(from_attributes=True)


class DebtStrategySettingUpdate(BaseModel):
    method: Optional[str] = None
    extra_monthly_amount: Optional[Decimal] = Field(default=None, ge=0)


class DebtPayoffProjectionEntry(BaseModel):
    debt_id: uuid.UUID
    creditor_name: str
    months_to_payoff: Optional[int]
    payoff_date: Optional[_Date]
    total_interest_remaining: Decimal


class DebtPayoffProjection(BaseModel):
    method: str
    extra_monthly_amount: Decimal
    order: list[DebtPayoffProjectionEntry]
    overall_payoff_date: Optional[_Date]
