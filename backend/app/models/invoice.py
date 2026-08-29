import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.invoice_line_item import InvoiceLineItem
    from app.models.invoice_payment import InvoicePayment
    from app.models.payee import Payee
    from app.models.user import User
    from app.models.workspace import Workspace


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    payee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payees.id"))
    
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    
    subtotal: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=0)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=0)
    
    issue_date: Mapped[_date] = mapped_column(Date)
    due_date: Mapped[_date] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    recurring_frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    recurring_end_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    next_recurrence_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)

    total_installments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    installment_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    payments: Mapped[list["InvoicePayment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    payee: Mapped["Payee"] = relationship()
    workspace: Mapped["Workspace"] = relationship()
    user: Mapped["User"] = relationship()
