"""Recurring invoices: an agreement that emits one invoice per period.

Two tables, and the rule that shapes both: **the schedule is an
agreement, never money**. Money only ever exists as an invoice, and an
invoice born from a schedule is an ordinary invoice that remembers
which agreement and which period it answers for. Everything a reader
wants to know about the agreement financially (recurring revenue,
what a client has paid under it, whether it is behind) is computed
from those invoices and never written here.

`InvoiceSchedule.status` therefore holds only decisions, exactly like
`Invoice.status`: someone paused it, someone ended it. "Past due" is a
fact about an invoice of this schedule and is derived.

The agreed price lives in **terms**, not on the schedule. A retainer
that goes from 3,000 to 3,500 in January is one agreement with two
terms, each saying from when it applies. That is what makes "what was
the deal in May" answerable without an invoice having been issued in
May, and what lets a raise be recorded today and take effect on the
next full period. Nothing is ever prorated: a change applies from a
period boundary, and a mid-period difference is an ordinary one-off
invoice or credit note.

This engine is its own, and is deliberately not `RecurringTransaction`.
That one materialises *placeholder transactions*, which matching
excludes because placeholders are not real money. A retainer emitting a
placeholder while matching binds real inflows to its invoice would make
the same expected money exist twice.
"""
import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.payee import Payee


#: Decisions a human takes about an agreement. Closed, decisions only.
#:
#:   active:  emitting. The job generates the next period when it is due.
#:   paused:  kept, not emitting. A person paused it, or the job did
#:             after repeated failures (see `pause_reason`).
#:   ended:   over. Terminal; carries `ended_at` and `end_reason`.
#:
#: `past_due` is deliberately absent: it is a fact about this schedule's
#: invoices, derived from them.
SCHEDULE_STATUSES = ("active", "paused", "ended")

#: Why an agreement ended. The one place a schedule records something
#: an invoice cannot: churn has a date and a side, and neither is
#: derivable from the invoices that stopped coming.
SCHEDULE_END_REASONS = (
    "canceled_by_client",
    "canceled_by_us",
    "completed",  # ran its term: `after_count` reached or `end_date` passed
    "unpaid",
    "other",
)

#: Why a schedule is paused. `failures` is the job giving up so a broken
#: agreement stops instead of retrying forever; `manual` is a person.
SCHEDULE_PAUSE_REASONS = ("manual", "failures")

#: Same vocabulary as recurring transactions, so the two pickers in the
#: product agree on what a word means.
SCHEDULE_FREQUENCIES = ("weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly")

#: How an agreement knows it is over. `never` runs until ended by hand.
SCHEDULE_END_TYPES = ("never", "on_date", "after_count")

#: Who authored the agreement. An `imported` schedule mirrors one a
#: gateway holds (a subscription on its side): that system owns the terms and
#: emits the invoices, Securo groups them and owns the cash. The job
#: never generates for an imported schedule.
SCHEDULE_ORIGINS = ("local", "imported")

#: How many periods each frequency puts in a year, used to normalise
#: any schedule to a monthly figure. Weekly and biweekly are 52 and 26
#: periods over twelve months, not 4 and 2 per month, because a month
#: is not four weeks and the difference is 8% a year.
PERIODS_PER_YEAR: dict[str, Decimal] = {
    "weekly": Decimal("52"),
    "biweekly": Decimal("26"),
    "monthly": Decimal("12"),
    "quarterly": Decimal("4"),
    "semiannual": Decimal("2"),
    "yearly": Decimal("1"),
}

#: After this many consecutive failures the job pauses the schedule
#: rather than retrying it daily forever.
MAX_CONSECUTIVE_FAILURES = 3


class InvoiceSchedule(Base):
    """One agreement with one client to bill them every period."""

    __tablename__ = "invoice_schedules"
    __table_args__ = (
        # A mirrored gateway subscription is identified by the source's
        # own id, so two syncs converge on one row (same rule as invoices).
        UniqueConstraint(
            "workspace_id",
            "external_source",
            "external_id",
            name="uq_invoice_schedules_workspace_external",
        ),
        # The job reads active schedules due on or before today. Nothing
        # else lists by this pair, so it is the only composite index.
        Index("ix_invoice_schedules_workspace_status", "workspace_id", "status"),
        CheckConstraint(
            "status IN ('active', 'paused', 'ended')", name="ck_invoice_schedules_status"
        ),
        CheckConstraint(
            "origin IN ('local', 'imported')", name="ck_invoice_schedules_origin"
        ),
        CheckConstraint(
            "frequency IN ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'yearly')",
            name="ck_invoice_schedules_frequency",
        ),
        CheckConstraint(
            "end_type IN ('never', 'on_date', 'after_count')",
            name="ck_invoice_schedules_end_type",
        ),
        # An end condition has to say what it is conditioned on. Written
        # as a constraint so no code path can leave `on_date` without a
        # date and have the job run forever.
        CheckConstraint(
            "(end_type = 'never')"
            " OR (end_type = 'on_date' AND end_date IS NOT NULL)"
            " OR (end_type = 'after_count' AND end_count IS NOT NULL AND end_count > 0)",
            name="ck_invoice_schedules_end_condition",
        ),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN"
            " ('canceled_by_client', 'canceled_by_us', 'completed', 'unpaid', 'other')",
            name="ck_invoice_schedules_end_reason",
        ),
        CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ('manual', 'failures')",
            name="ck_invoice_schedules_pause_reason",
        ),
        # An ended schedule says when and why; a live one says neither.
        CheckConstraint(
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)"
            " OR (status <> 'ended' AND ended_at IS NULL AND end_reason IS NULL)",
            name="ck_invoice_schedules_ended_fields",
        ),
        CheckConstraint("next_sequence >= 1", name="ck_invoice_schedules_next_sequence"),
        CheckConstraint(
            "payment_terms_days IS NULL OR payment_terms_days >= 0",
            name="ck_invoice_schedules_payment_terms",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Who created it. Not an owner: the agreement belongs to the
    # workspace and outlives any member.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # RESTRICT for the same reason as on invoices: deleting a client must
    # never silently delete the record of what they agreed to pay.
    payee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payees.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    # What the agreement is called: "Plano Pro", "Retainer mensal". The
    # invoices it emits carry the client's name; this is for the list of
    # agreements, where a client may have more than one.
    name: Mapped[str] = mapped_column(String(200))

    origin: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    external_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    pause_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ended_at: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    end_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # The calendar. `start_date` is the anchor: period `n` (1-based)
    # starts at `start_date` advanced `n - 1` periods, with the day of
    # month clamped in shorter months and recovered afterwards, exactly
    # as recurring transactions do it. Both are frozen once an invoice
    # exists under the schedule, because moving the anchor would change
    # what period every existing invoice answers for.
    frequency: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[_date] = mapped_column(Date)
    end_type: Mapped[str] = mapped_column(String(20), default="never", server_default="never")
    end_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    end_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Null means the workspace default at the moment each invoice is
    # generated, so a change in settings reaches every retainer that
    # never said otherwise.
    payment_terms_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Copied onto every generated invoice. The lines are not here: they
    # belong to a term, because they change and these do not.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Engine state. `next_sequence` is the period the job will emit
    # next; every period below it is either already an invoice or was
    # deliberately skipped when the schedule was created with a start in
    # the past (those are history, linked by hand if the user wants
    # them). Together with the unique `(schedule_id, sequence)` on
    # invoices it is what makes a retried job a no-op.
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    payee: Mapped[Optional["Payee"]] = relationship(lazy="joined")
    terms: Mapped[list["InvoiceScheduleTerm"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="InvoiceScheduleTerm.effective_from",
        lazy="selectin",
    )
    # Not cascaded: the invoices are money and outlive the agreement.
    # The foreign key is SET NULL, so deleting a schedule orphans its
    # invoices rather than deleting a record of what was owed; the
    # service refuses the delete when any exist anyway.
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="schedule",
        order_by="Invoice.sequence",
        lazy="noload",
    )


class InvoiceScheduleTerm(Base):
    """What the agreement says from a given date on.

    One row per change of price. The job picks, for each period, the
    term with the latest `effective_from` on or before the period start,
    so a raise recorded in September for January changes nothing until
    January and every January invoice says 3,500 without anybody
    remembering to do anything.

    Lines are snapshotted as JSON in the same shape `InvoiceLineInput`
    accepts, and copied into real `invoice_lines` rows at generation.
    The invoice, not the term, is the record of what was actually
    billed: editing a future term rewrites nothing that was issued.
    """

    __tablename__ = "invoice_schedule_terms"
    __table_args__ = (
        # Two prices from the same day would be two answers to "what does
        # this cost", and the job would have to pick one arbitrarily.
        UniqueConstraint(
            "schedule_id", "effective_from", name="uq_invoice_schedule_terms_effective_from"
        ),
        Index("ix_invoice_schedule_terms_schedule", "schedule_id"),
        CheckConstraint("discount >= 0", name="ck_invoice_schedule_terms_discount"),
        CheckConstraint("total >= 0", name="ck_invoice_schedule_terms_total"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_schedules.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    effective_from: Mapped[_date] = mapped_column(Date)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    discount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    # Derived from the lines at write time and stored so recurring
    # revenue is one SUM over this table rather than a walk through
    # every term's JSON. Recomputed whenever the lines change; never
    # accepted from a caller.
    subtotal: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    schedule: Mapped["InvoiceSchedule"] = relationship(back_populates="terms")
