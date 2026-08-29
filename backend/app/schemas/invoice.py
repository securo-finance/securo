import uuid
from datetime import date as _date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLineItemBase(BaseModel):
    description: str = Field(max_length=500)
    quantity: Decimal = Field(default=Decimal("1.0000"), max_digits=10, decimal_places=4)
    unit_price: Decimal = Field(max_digits=15, decimal_places=2)
    amount: Decimal = Field(max_digits=15, decimal_places=2)
    category_id: Optional[uuid.UUID] = None
    sort_order: int = 0


class InvoiceLineItemCreate(InvoiceLineItemBase):
    pass


class InvoiceLineItemUpdate(InvoiceLineItemBase):
    pass


class InvoiceLineItemRead(InvoiceLineItemBase):
    id: uuid.UUID
    invoice_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class InvoicePaymentBase(BaseModel):
    amount: Decimal = Field(max_digits=15, decimal_places=2)
    date: _date
    notes: Optional[str] = Field(default=None, max_length=500)
    transaction_id: Optional[uuid.UUID] = None


class InvoicePaymentCreate(InvoicePaymentBase):
    pass


class InvoicePaymentRead(InvoicePaymentBase):
    id: uuid.UUID
    invoice_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class InvoiceBase(BaseModel):
    payee_id: uuid.UUID
    invoice_number: str = Field(max_length=50)
    currency: str = Field(default="USD", max_length=3)
    
    subtotal: Decimal = Field(default=Decimal("0.00"), max_digits=15, decimal_places=2)
    total: Decimal = Field(default=Decimal("0.00"), max_digits=15, decimal_places=2)
    
    issue_date: _date
    due_date: _date
    notes: Optional[str] = Field(default=None, max_length=2000)

    is_recurring: bool = False
    recurring_frequency: Optional[Literal["monthly", "quarterly", "weekly", "yearly"]] = None
    recurring_end_date: Optional[_date] = None

    total_installments: Optional[int] = Field(default=None, ge=1)
    installment_amount: Optional[Decimal] = Field(default=None, max_digits=15, decimal_places=2)


class InvoiceCreate(InvoiceBase):
    status: Literal["draft", "sent", "partial", "paid", "overdue", "cancelled"] = "draft"
    line_items: List[InvoiceLineItemCreate] = []


class InvoiceUpdate(BaseModel):
    payee_id: Optional[uuid.UUID] = None
    invoice_number: Optional[str] = None
    currency: Optional[str] = None
    
    subtotal: Optional[Decimal] = None
    total: Optional[Decimal] = None
    
    issue_date: Optional[_date] = None
    due_date: Optional[_date] = None
    notes: Optional[str] = None

    is_recurring: Optional[bool] = None
    recurring_frequency: Optional[Literal["monthly", "quarterly", "weekly", "yearly"]] = None
    recurring_end_date: Optional[_date] = None
    
    line_items: Optional[List[InvoiceLineItemUpdate]] = None


class InvoiceSummary(InvoiceBase):
    """Summarized version for list views (no line items or payments array)."""
    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    status: Literal["draft", "sent", "partial", "paid", "overdue", "cancelled"]
    amount_paid: Decimal
    amount_due: Decimal
    next_recurrence_date: Optional[_date] = None
    created_at: datetime
    updated_at: datetime
    
    payee_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class InvoiceRead(InvoiceSummary):
    """Detailed version including nested items."""
    line_items: List[InvoiceLineItemRead] = []
    payments: List[InvoicePaymentRead] = []

    model_config = ConfigDict(from_attributes=True)
