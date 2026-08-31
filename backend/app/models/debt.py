"""Debt management — loans, payroll-deducted loans, overdue credit cards.

Isolated from the regular financial ledger on purpose: a `Debt` never
touches `accounts`/`transactions` by itself. A real `Transaction` only
gets linked when a `DebtInstallment` is actually paid manually (same
pattern as `GroupSettlement.transaction_id` — nullable, SET NULL on
delete). Payroll-deducted installments (consignado) never get a linked
transaction at all: the deduction happens before the salary is
deposited, so it never appears in the bank history to link against.
"""
import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.transaction import Transaction


class Debt(Base):
    __tablename__ = "debts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('loan', 'payroll_loan', 'credit_card_overdue', 'other')",
            name="ck_debts_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'negotiating', 'paid_off', 'defaulted')",
            name="ck_debts_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    creditor_name: Mapped[str] = mapped_column(String(255))
    contract_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    original_principal: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    current_balance: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    # Optional, context-only reference to an existing account (e.g. a
    # synced credit card that went into default). Never used to derive
    # balance — `current_balance` above is the sole source of truth.
    related_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    opened_date: Mapped[_date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    related_account: Mapped[Optional["Account"]] = relationship()
    plans: Mapped[list["DebtPlan"]] = relationship(
        back_populates="debt", cascade="all, delete-orphan", order_by="DebtPlan.created_at"
    )


class DebtPlan(Base):
    """One way of paying off a `Debt` — the original contract, a
    renegotiation, or a what-if simulation. Only one plan per debt may
    be `active`; switching plans (e.g. accepting a renegotiation) marks
    the previous active plan `superseded` rather than deleting it, so
    the payoff history stays intact.
    """

    __tablename__ = "debt_plans"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('original_contract', 'renegotiated', 'simulation')",
            name="ck_debt_plans_kind",
        ),
        CheckConstraint(
            "status IN ('proposed', 'active', 'superseded', 'rejected')",
            name="ck_debt_plans_status",
        ),
        CheckConstraint(
            "collection_mode IN ('payroll_deduction', 'manual')",
            name="ck_debt_plans_collection_mode",
        ),
        CheckConstraint(
            "frequency IN ('weekly', 'monthly', 'quarterly', 'yearly')",
            name="ck_debt_plans_frequency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    debt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("debts.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    collection_mode: Mapped[str] = mapped_column(String(20), default="manual")
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(precision=8, scale=4), default=Decimal("0"))
    installment_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    num_installments: Mapped[int] = mapped_column(Integer)
    first_due_date: Mapped[_date] = mapped_column(Date)
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    debt: Mapped["Debt"] = relationship(back_populates="plans")
    installments: Mapped[list["DebtInstallment"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="DebtInstallment.installment_number"
    )


class DebtInstallment(Base):
    __tablename__ = "debt_installments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'late', 'skipped')",
            name="ck_debt_installments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("debt_plans.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    installment_number: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[_date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    principal_portion: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    interest_portion: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    # Nullable, SET NULL on delete — same shape as GroupSettlement.transaction_id.
    # Left null for payroll_deduction installments: there is never a real
    # transaction to link. Optional even for manual installments (the user
    # may record a payment without bothering to link the bank entry).
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    plan: Mapped["DebtPlan"] = relationship(back_populates="installments")
    transaction: Mapped[Optional["Transaction"]] = relationship(foreign_keys=[transaction_id])


class DebtStrategySetting(Base):
    """One row per workspace: which payoff strategy to project with."""

    __tablename__ = "debt_strategy_settings"
    __table_args__ = (
        CheckConstraint(
            "method IN ('snowball', 'avalanche')",
            name="ck_debt_strategy_settings_method",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True
    )
    method: Mapped[str] = mapped_column(String(20), default="avalanche")
    extra_monthly_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
