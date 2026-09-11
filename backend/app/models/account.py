import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.bank_connection import BankConnection
    from app.models.institution import Institution
    from app.models.loan_schedule import LoanAmortizationSchedule
    from app.models.loan_prepayment import LoanPrepayment
    from app.models.loan_plan_commitment import LoanPlanCommitment
    from app.models.transaction import Transaction


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_connections.id"), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Last 4 chars of the bank's identifier (IBAN, account or card number), when
    # the provider exposes one. Provider-owned like `name`: refreshed on sync,
    # not user-editable. Never holds the full identifier.
    masked_number: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    type: Mapped[str] = mapped_column(String(50))  # checking, savings, credit_card, loan, investment, wallet
    balance: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    balance_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    statement_close_day: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    payment_due_day: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    minimum_payment: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    card_brand: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    card_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Loan products (type="loan"). Outstanding lives in `balance` with the
    # same positive-for-debt convention as credit cards.
    loan_kind: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    original_principal: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True
    )
    interest_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=8, scale=4), nullable=True
    )
    tenure_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    emi_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True
    )
    disbursed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    emi_day: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    current_schedule_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_payment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    total_prepayments: Mapped[Decimal] = mapped_column(
        Numeric(precision=15, scale=2), default=Decimal("0.00"), server_default="0.00"
    )
    # The institution this account is actually at. One connection can span
    # several (SimpleFIN — issue #345); null falls back to the connection's
    # own institution_name/logo_url. Eager (selectin) because serialization
    # always reads it and lazy loads raise in async sessions.
    institution_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    connection: Mapped[Optional["BankConnection"]] = relationship(back_populates="accounts")
    institution: Mapped[Optional["Institution"]] = relationship(
        back_populates="accounts", lazy="selectin"
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    loan_schedules: Mapped[list["LoanAmortizationSchedule"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    loan_prepayments: Mapped[list["LoanPrepayment"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    loan_plan_commitments: Mapped[list["LoanPlanCommitment"]] = relationship(
        foreign_keys="LoanPlanCommitment.account_id",
        back_populates="account",
        cascade="all, delete-orphan",
    )
