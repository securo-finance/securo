"""Recurring invoices: the agreement, its price over time, and the job.

The organising rule is the ledger's own, applied one level up: **the
schedule is an agreement, the invoice is the money**. Nothing here
counts as revenue, owed or paid; every such figure is computed from the
invoices a schedule emitted or was given. What the schedule adds is the
one thing invoices cannot say: what was agreed, from when, and when and
why it stopped.

## Periods

Period `n` (1-based) starts at `start_date` advanced `n - 1` times by
the frequency, day of month clamped in shorter months and recovered
afterwards, and ends the day before period `n + 1` starts. That is all
the calendar there is: no separate "next run" date to drift, no
timezone column to disagree with the anchor. The job compares period
starts against the UTC date; a retainer due on the 1st is emitted on
the 1st UTC, which is the same day everywhere that matters for a
document dated by the day.

## Terms

The price is a list of terms, each `effective_from` a date. A period is
priced by the latest term on or before its start. A term may be added,
changed or removed only while no invoice answers for a period it
governs; once one does, the term is history and history is not edited.
There is no proration by design: a change applies from a period
boundary, and a mid-period difference is an ordinary one-off invoice.

## The cursor

`next_sequence` is the period the job emits next. At creation it is the
first period starting on or after today: a schedule anchored in the
past does not back-fill months the user may have billed by hand (those
are linked, see `link_invoice`). Together with the unique
`(schedule_id, sequence)` on invoices, the cursor is what makes a
retried or doubled job a no-op.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date as _date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceAllocation, InvoiceSettings
from app.models.invoice_schedule import (
    MAX_CONSECUTIVE_FAILURES,
    PERIODS_PER_YEAR,
    SCHEDULE_END_REASONS,
    SCHEDULE_END_TYPES,
    SCHEDULE_FREQUENCIES,
    InvoiceSchedule,
    InvoiceScheduleTerm,
)
from app.models.workspace import Workspace
from app.services import invoice_archive, invoice_service, reconciliation_service
from app.services.invoice_service import InvoiceError
from app.services.recurring_transaction_service import _advance_date

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

#: How many periods one run may emit for one schedule. The cursor
#: starts at today, so this only matters when the worker was down for
#: a while; the cap keeps a schedule that somehow fell far behind from
#: flooding a workspace in one pass, and the next run continues.
MAX_PERIODS_PER_RUN = 12

#: A guard on every calendar walk. A weekly schedule looked at ten
#: years out is 520 steps; nothing legitimate needs more.
_MAX_WALK = 1000


def _today() -> _date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------
def period_start(schedule: InvoiceSchedule, sequence: int) -> _date:
    """The first day of period `sequence` (1-based)."""
    if sequence < 1:
        raise ValueError("sequence is 1-based")
    current = schedule.start_date
    for _ in range(sequence - 1):
        current = _advance_date(current, schedule.frequency, intended_day=schedule.start_date.day)
    return current


def period_bounds(schedule: InvoiceSchedule, sequence: int) -> tuple[_date, _date]:
    """First and last day of a period, both inclusive."""
    start = period_start(schedule, sequence)
    following = _advance_date(start, schedule.frequency, intended_day=schedule.start_date.day)
    return start, following - timedelta(days=1)


def first_sequence_on_or_after(schedule: InvoiceSchedule, floor: _date) -> int:
    """The first period starting on or after `floor`."""
    sequence = 1
    current = schedule.start_date
    while current < floor and sequence < _MAX_WALK:
        current = _advance_date(current, schedule.frequency, intended_day=schedule.start_date.day)
        sequence += 1
    return sequence


def sequence_for_period_start(schedule: InvoiceSchedule, start: _date) -> Optional[int]:
    """Which period starts on `start`, or None if no period does.

    The inverse of `period_start`, used when a person points at an
    invoice and says "this is September": the date has to be a real
    boundary of this schedule, not a day somewhere inside a period.
    """
    if start < schedule.start_date:
        return None
    sequence = first_sequence_on_or_after(schedule, start)
    return sequence if period_start(schedule, sequence) == start else None


def period_within_end(schedule: InvoiceSchedule, sequence: int) -> bool:
    """Whether the agreement still covers period `sequence`."""
    if schedule.end_type == "after_count":
        return sequence <= (schedule.end_count or 0)
    if schedule.end_type == "on_date":
        return period_start(schedule, sequence) <= (schedule.end_date or schedule.start_date)
    return True


def next_period_start(schedule: InvoiceSchedule) -> Optional[_date]:
    """When the next invoice would be dated, or None if the agreement
    has nothing left to emit."""
    if schedule.status == "ended" or not period_within_end(schedule, schedule.next_sequence):
        return None
    return period_start(schedule, schedule.next_sequence)


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------
def term_for(schedule: InvoiceSchedule, on: _date) -> Optional[InvoiceScheduleTerm]:
    """The term in force on a date: the latest one starting on or before it."""
    chosen: Optional[InvoiceScheduleTerm] = None
    for term in schedule.terms:
        if term.effective_from <= on and (chosen is None or term.effective_from > chosen.effective_from):
            chosen = term
    return chosen


def current_term(schedule: InvoiceSchedule, today: Optional[_date] = None) -> Optional[InvoiceScheduleTerm]:
    return term_for(schedule, today or _today())


def upcoming_terms(schedule: InvoiceSchedule, today: Optional[_date] = None) -> list[InvoiceScheduleTerm]:
    """Terms that have not started yet: the recorded raises."""
    today = today or _today()
    return [t for t in schedule.terms if t.effective_from > today]


def monthly_amount(schedule: InvoiceSchedule, term: Optional[InvoiceScheduleTerm]) -> Decimal:
    """What this agreement is worth per month at a given term.

    A weekly retainer is not four times its amount: it is 52 periods
    over 12 months. Normalising through the year is what makes a
    quarterly and a monthly agreement add up to one honest figure.
    """
    if term is None:
        return ZERO
    per_year = PERIODS_PER_YEAR[schedule.frequency]
    return (Decimal(term.total) * per_year / Decimal("12")).quantize(CENT)


def _term_totals(lines: list[dict[str, Any]], discount: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Subtotal, tax and total of a term's lines, exactly as the invoice
    will compute them at generation. Same arithmetic as
    `invoice_service._recompute_totals`, on dicts instead of rows."""
    subtotal = ZERO
    tax_total = ZERO
    for line in lines:
        quantity = Decimal(str(line.get("quantity", 1)))
        unit_price = Decimal(str(line.get("unit_price", 0)))
        line_total = (quantity * unit_price).quantize(CENT)
        subtotal += line_total
        if line.get("tax_rate") is not None:
            tax_total += (line_total * Decimal(str(line["tax_rate"])) / Decimal("100")).quantize(CENT)
    total = subtotal - discount + tax_total
    if total < ZERO:
        raise InvoiceError(
            "discount_exceeds_total", "The discount is larger than the amount being billed"
        )
    return subtotal, tax_total, total


def _normalise_lines(lines: Any) -> list[dict[str, Any]]:
    """Lines as they will be stored: plain JSON, decimals as strings."""
    if not lines:
        raise InvoiceError("term_lines_required", "A term needs at least one line")
    out: list[dict[str, Any]] = []
    for raw in lines:
        line = dict(raw)
        if not line.get("description"):
            raise InvoiceError("term_lines_required", "Every line needs a description")
        out.append(
            {
                "description": line["description"],
                "quantity": str(Decimal(str(line.get("quantity", 1)))),
                "unit": line.get("unit") or None,
                "unit_price": str(Decimal(str(line.get("unit_price", 0)))),
                "tax_rate": (
                    str(Decimal(str(line["tax_rate"]))) if line.get("tax_rate") is not None else None
                ),
            }
        )
    return out


def _fill_term(term: InvoiceScheduleTerm, lines: Any, discount: Any) -> None:
    term.lines = _normalise_lines(lines)
    term.discount = Decimal(str(discount or 0))
    if term.discount < ZERO:
        raise InvoiceError("negative_discount", "A discount cannot be negative")
    term.subtotal, term.tax_total, term.total = _term_totals(term.lines, term.discount)
    # A zero-value term would emit invoices the ledger refuses to issue.
    # Refused here, where the person can fix it, instead of on the day
    # the job trips over it.
    if term.total <= ZERO:
        raise InvoiceError("term_empty", "A term must bill something")


async def _latest_linked_period_start(
    session: AsyncSession, schedule_id: uuid.UUID
) -> Optional[_date]:
    result = await session.execute(
        select(func.max(Invoice.period_start)).where(Invoice.schedule_id == schedule_id)
    )
    return result.scalar_one_or_none()


async def _assert_term_editable(
    session: AsyncSession, schedule: InvoiceSchedule, effective_from: _date
) -> None:
    """A term governs every period starting on or after its date. Once
    an invoice exists for such a period, the term is history."""
    latest = await _latest_linked_period_start(session, schedule.id)
    if latest is not None and latest >= effective_from:
        raise InvoiceError(
            "term_in_use",
            "An invoice was already issued under this term; add a new term from a later date",
        )


async def add_term(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    *,
    effective_from: _date,
    lines: Any,
    discount: Any = None,
) -> InvoiceScheduleTerm:
    """Record what the agreement says from a date on.

    Adding on a date that already has a term replaces that term, so
    "actually, make January 3,600 not 3,500" is one call and not a
    delete plus a create. Either way the date must be one no invoice
    has been issued under.
    """
    if schedule.status == "ended":
        raise InvoiceError("schedule_ended", "An ended agreement takes no new terms")
    if effective_from < schedule.start_date:
        raise InvoiceError(
            "term_before_start", "A term cannot start before the agreement does"
        )
    await _assert_term_editable(session, schedule, effective_from)

    existing = next((t for t in schedule.terms if t.effective_from == effective_from), None)
    term = existing or InvoiceScheduleTerm(
        schedule_id=schedule.id,
        workspace_id=schedule.workspace_id,
        effective_from=effective_from,
    )
    _fill_term(term, lines, discount)
    if existing is None:
        session.add(term)
        schedule.terms.append(term)
    await session.flush()
    return term


async def update_term(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    term: InvoiceScheduleTerm,
    *,
    lines: Any = None,
    discount: Any = None,
    effective_from: Optional[_date] = None,
) -> InvoiceScheduleTerm:
    if term.schedule_id != schedule.id:
        raise InvoiceError("term_not_found", "Term not found on this agreement", 404)
    await _assert_term_editable(session, schedule, term.effective_from)
    if effective_from is not None and effective_from != term.effective_from:
        # The term in force from the start is what prices the first
        # periods. Moving it later would leave them with no price, and
        # the job would fail on each until it paused the agreement.
        if term.effective_from == schedule.start_date:
            raise InvoiceError("first_term", "The price in force from the start cannot be moved")
        if effective_from < schedule.start_date:
            raise InvoiceError(
                "term_before_start", "A term cannot start before the agreement does"
            )
        await _assert_term_editable(session, schedule, effective_from)
        if any(t.effective_from == effective_from for t in schedule.terms if t.id != term.id):
            raise InvoiceError("term_date_taken", "Another term already starts on that date", 409)
        term.effective_from = effective_from
    _fill_term(
        term,
        lines if lines is not None else term.lines,
        discount if discount is not None else term.discount,
    )
    await session.flush()
    return term


async def delete_term(
    session: AsyncSession, schedule: InvoiceSchedule, term: InvoiceScheduleTerm
) -> None:
    if term.schedule_id != schedule.id:
        raise InvoiceError("term_not_found", "Term not found on this agreement", 404)
    if len(schedule.terms) <= 1:
        raise InvoiceError("last_term", "An agreement needs at least one term")
    if term.effective_from == schedule.start_date:
        raise InvoiceError("first_term", "The price in force from the start cannot be removed")
    await _assert_term_editable(session, schedule, term.effective_from)
    schedule.terms.remove(term)
    await session.delete(term)
    await session.flush()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def get_schedule(
    session: AsyncSession, schedule_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[InvoiceSchedule]:
    result = await session.execute(
        select(InvoiceSchedule).where(
            InvoiceSchedule.id == schedule_id, InvoiceSchedule.workspace_id == workspace_id
        )
    )
    return result.unique().scalar_one_or_none()


async def find_by_external_id(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    external_source: Optional[str],
    external_id: Optional[str],
) -> Optional[InvoiceSchedule]:
    if not external_source or not external_id:
        return None
    result = await session.execute(
        select(InvoiceSchedule).where(
            InvoiceSchedule.workspace_id == workspace_id,
            InvoiceSchedule.external_source == external_source,
            InvoiceSchedule.external_id == external_id,
        )
    )
    return result.unique().scalar_one_or_none()


async def list_schedules(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    status: Optional[str] = None,
    payee_id: Optional[uuid.UUID] = None,
) -> list[InvoiceSchedule]:
    query = select(InvoiceSchedule).where(InvoiceSchedule.workspace_id == workspace_id)
    if status:
        query = query.where(InvoiceSchedule.status == status)
    if payee_id:
        query = query.where(InvoiceSchedule.payee_id == payee_id)
    query = query.order_by(InvoiceSchedule.created_at.desc())
    result = await session.execute(query)
    return list(result.unique().scalars().all())


@dataclass(frozen=True)
class ScheduleFigures:
    """What the invoices say about one agreement. Never stored."""

    invoice_count: int
    amount_invoiced: Decimal
    amount_paid: Decimal
    past_due_count: int


async def figures_for(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    schedule_ids: list[uuid.UUID],
    today: Optional[_date] = None,
) -> dict[uuid.UUID, ScheduleFigures]:
    """The derived money facts for many schedules in two queries.

    Void and uncollectible invoices count towards nothing here, for the
    same reason they read as zero everywhere else. Drafts are counted as
    invoices but not as money, since nothing is owed yet.
    """
    today = today or _today()
    empty = ScheduleFigures(0, ZERO, ZERO, 0)
    if not schedule_ids:
        return {}

    allocated = (
        select(
            InvoiceAllocation.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceAllocation.amount), 0).label("paid"),
        )
        .group_by(InvoiceAllocation.invoice_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                Invoice.schedule_id,
                Invoice.status,
                Invoice.due_date,
                Invoice.total,
                func.coalesce(allocated.c.paid, 0).label("paid"),
            )
            .outerjoin(allocated, allocated.c.invoice_id == Invoice.id)
            .where(
                Invoice.workspace_id == workspace_id,
                Invoice.schedule_id.in_(schedule_ids),
            )
        )
    ).all()

    counts: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        acc = counts.setdefault(
            row.schedule_id,
            {"n": 0, "invoiced": ZERO, "paid": ZERO, "past_due": 0},
        )
        acc["n"] += 1
        if row.status != "open":
            continue
        total = Decimal(str(row.total))
        paid = Decimal(str(row.paid))
        acc["invoiced"] += total
        acc["paid"] += min(paid, total)
        if row.due_date < today and paid < total:
            acc["past_due"] += 1

    return {
        sid: ScheduleFigures(
            invoice_count=acc["n"],
            amount_invoiced=acc["invoiced"],
            amount_paid=acc["paid"],
            past_due_count=acc["past_due"],
        )
        if (acc := counts.get(sid))
        else empty
        for sid in schedule_ids
    }


@dataclass(frozen=True)
class CurrencySummary:
    currency: str
    #: Monthly recurring revenue: every active agreement at its current
    #: term, normalised to a month.
    monthly_recurring: Decimal
    active_count: int
    #: Agreements that ended in the trailing 30 days, and what they were
    #: worth per month when they did. The churn figure.
    ended_recently_count: int
    monthly_lost: Decimal
    #: Active agreements with at least one invoice past its due date.
    past_due_count: int


@dataclass(frozen=True)
class ScheduleSummary:
    active_count: int
    paused_count: int
    ended_count: int
    by_currency: list[CurrencySummary]


async def summary(
    session: AsyncSession, workspace_id: uuid.UUID, today: Optional[_date] = None
) -> ScheduleSummary:
    today = today or _today()
    schedules = await list_schedules(session, workspace_id)
    active = [s for s in schedules if s.status == "active"]
    figures = await figures_for(session, workspace_id, [s.id for s in active], today)
    since = today - timedelta(days=30)

    buckets: dict[str, dict[str, Any]] = {}

    def bucket(currency: str) -> dict[str, Any]:
        return buckets.setdefault(
            currency,
            {"mrr": ZERO, "active": 0, "ended": 0, "lost": ZERO, "past_due": 0},
        )

    for s in schedules:
        if s.status == "active":
            b = bucket(s.currency)
            b["mrr"] += monthly_amount(s, term_for(s, today))
            b["active"] += 1
            if figures[s.id].past_due_count:
                b["past_due"] += 1
        elif s.status == "ended" and s.ended_at and since <= s.ended_at <= today:
            b = bucket(s.currency)
            b["ended"] += 1
            b["lost"] += monthly_amount(s, term_for(s, s.ended_at))

    return ScheduleSummary(
        active_count=len(active),
        paused_count=sum(1 for s in schedules if s.status == "paused"),
        ended_count=sum(1 for s in schedules if s.status == "ended"),
        by_currency=[
            CurrencySummary(
                currency=currency,
                monthly_recurring=b["mrr"],
                active_count=b["active"],
                ended_recently_count=b["ended"],
                monthly_lost=b["lost"],
                past_due_count=b["past_due"],
            )
            for currency, b in sorted(buckets.items())
        ],
    )


# ---------------------------------------------------------------------------
# Writing the agreement
# ---------------------------------------------------------------------------
def _validate_end(end_type: str, end_date: Optional[_date], end_count: Optional[int]) -> None:
    if end_type not in SCHEDULE_END_TYPES:
        raise InvoiceError("invalid_end_type", "Unknown end condition")
    if end_type == "on_date" and end_date is None:
        raise InvoiceError("end_date_required", "Say on which date the agreement ends")
    if end_type == "after_count" and (end_count is None or end_count < 1):
        raise InvoiceError("end_count_required", "Say after how many invoices the agreement ends")


async def _assert_payee(
    session: AsyncSession, payee_id: Optional[uuid.UUID], workspace_id: uuid.UUID
) -> None:
    await invoice_service._assert_payee(session, payee_id, workspace_id)


async def create_schedule(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    data: dict[str, Any],
    today: Optional[_date] = None,
) -> InvoiceSchedule:
    """A new agreement with its first term.

    The cursor starts at the first period on or after today. A start in
    the past is allowed (the retainer began in March) and does not
    back-fill: the months already billed by hand are linked with
    `link_invoice`, and the job takes over from here.
    """
    today = today or _today()
    frequency = data.get("frequency")
    if frequency not in SCHEDULE_FREQUENCIES:
        raise InvoiceError("invalid_frequency", "Unknown frequency")
    start_date = data.get("start_date") or today
    end_type = data.get("end_type") or "never"
    _validate_end(end_type, data.get("end_date"), data.get("end_count"))
    if end_type == "on_date" and data["end_date"] < start_date:
        raise InvoiceError("end_before_start", "The agreement cannot end before it starts")
    await _assert_payee(session, data.get("payee_id"), workspace_id)

    origin = data.get("origin") or "local"
    if origin == "imported" and not data.get("external_source"):
        raise InvoiceError(
            "external_source_required", "An imported agreement must say where it came from"
        )
    name = (data.get("name") or "").strip()
    if not name:
        raise InvoiceError("name_required", "Give the agreement a name")

    schedule = InvoiceSchedule(
        workspace_id=workspace_id,
        user_id=user_id,
        payee_id=data.get("payee_id"),
        name=name,
        origin=origin,
        external_source=data.get("external_source"),
        external_id=data.get("external_id"),
        status="active",
        frequency=frequency,
        start_date=start_date,
        end_type=end_type,
        end_date=data.get("end_date") if end_type == "on_date" else None,
        end_count=data.get("end_count") if end_type == "after_count" else None,
        payment_terms_days=data.get("payment_terms_days"),
        currency=(data.get("currency") or "USD").upper(),
        notes=data.get("notes"),
        custom_fields=data.get("custom_fields"),
    )
    schedule.terms = []
    schedule.next_sequence = first_sequence_on_or_after(schedule, today)
    session.add(schedule)
    await session.flush()

    term = InvoiceScheduleTerm(
        schedule_id=schedule.id,
        workspace_id=workspace_id,
        effective_from=start_date,
    )
    _fill_term(term, data.get("lines"), data.get("discount"))
    session.add(term)
    schedule.terms.append(term)
    await session.flush()
    return schedule


#: Frozen once any invoice answers for a period: moving the anchor or
#: changing the frequency would change what period every existing
#: invoice was for.
_CALENDAR_FIELDS = ("frequency", "start_date")


async def update_schedule(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    data: dict[str, Any],
    today: Optional[_date] = None,
) -> InvoiceSchedule:
    today = today or _today()
    if schedule.status == "ended":
        raise InvoiceError("schedule_ended", "An ended agreement cannot be edited")

    touches_calendar = any(
        data.get(f) is not None and data[f] != getattr(schedule, f) for f in _CALENDAR_FIELDS
    )
    if touches_calendar:
        latest = await _latest_linked_period_start(session, schedule.id)
        if latest is not None:
            raise InvoiceError(
                "schedule_locked",
                "The frequency and start date are fixed once an invoice exists under this agreement",
            )
        if data.get("frequency") is not None:
            if data["frequency"] not in SCHEDULE_FREQUENCIES:
                raise InvoiceError("invalid_frequency", "Unknown frequency")
            schedule.frequency = data["frequency"]
        if data.get("start_date") is not None:
            # The first term moves with the anchor: it is the price
            # "from the start", and the start moved.
            first = min(schedule.terms, key=lambda t: t.effective_from)
            if any(t.effective_from <= data["start_date"] for t in schedule.terms if t is not first):
                raise InvoiceError(
                    "term_before_start", "A term would start before the agreement does"
                )
            first.effective_from = data["start_date"]
            schedule.start_date = data["start_date"]
        schedule.next_sequence = first_sequence_on_or_after(schedule, today)

    if "payee_id" in data:
        await _assert_payee(session, data["payee_id"], schedule.workspace_id)
        schedule.payee_id = data["payee_id"]
    if data.get("name") is not None:
        name = data["name"].strip()
        if not name:
            raise InvoiceError("name_required", "Give the agreement a name")
        schedule.name = name
    if data.get("currency") is not None:
        latest = await _latest_linked_period_start(session, schedule.id)
        if latest is not None and data["currency"].upper() != schedule.currency:
            raise InvoiceError(
                "schedule_locked", "The currency is fixed once an invoice exists under this agreement"
            )
        schedule.currency = data["currency"].upper()
    for field in ("payment_terms_days", "notes", "custom_fields"):
        if field in data:
            setattr(schedule, field, data[field])

    if any(k in data for k in ("end_type", "end_date", "end_count")):
        end_type = data.get("end_type") or schedule.end_type
        end_date = data["end_date"] if "end_date" in data else schedule.end_date
        end_count = data["end_count"] if "end_count" in data else schedule.end_count
        _validate_end(end_type, end_date, end_count)
        if end_type == "on_date" and end_date is not None and end_date < schedule.start_date:
            raise InvoiceError("end_before_start", "The agreement cannot end before it starts")
        schedule.end_type = end_type
        schedule.end_date = end_date if end_type == "on_date" else None
        schedule.end_count = end_count if end_type == "after_count" else None
        # An end condition already behind the cursor ends the agreement
        # now rather than on the next job run: the user asked for it to
        # be over, and a screen that says "active" until tomorrow lies.
        if schedule.status == "active":
            _complete_if_over(schedule, today)

    await session.flush()
    return schedule


def _complete_if_over(schedule: InvoiceSchedule, today: _date) -> bool:
    """End the agreement as `completed` when its own condition is met."""
    if not period_within_end(schedule, schedule.next_sequence):
        schedule.status = "ended"
        schedule.ended_at = today
        schedule.end_reason = "completed"
        schedule.pause_reason = None
        return True
    return False


async def pause_schedule(session: AsyncSession, schedule: InvoiceSchedule) -> InvoiceSchedule:
    if schedule.status == "ended":
        raise InvoiceError("schedule_ended", "An ended agreement cannot be paused")
    schedule.status = "paused"
    schedule.pause_reason = "manual"
    await session.flush()
    return schedule


async def resume_schedule(
    session: AsyncSession, schedule: InvoiceSchedule, today: Optional[_date] = None
) -> InvoiceSchedule:
    """Back to emitting, from the next period on.

    Periods that fell during a manual pause are not emitted: a pause is
    "stop billing", and billing the gap afterwards would be the
    opposite. The cursor skips to the first period on or after today,
    and anything before it is history the user may link by hand.

    A pause for failures is different: the job gave up on a period that
    was already due, and resuming is the person saying the cause is
    fixed. The cursor stays on that period so it is billed, not dropped.
    """
    today = today or _today()
    if schedule.status == "ended":
        raise InvoiceError("schedule_ended", "An ended agreement cannot be resumed")
    paused_by_failures = schedule.pause_reason == "failures"
    schedule.status = "active"
    schedule.pause_reason = None
    schedule.consecutive_failures = 0
    if not paused_by_failures:
        schedule.next_sequence = max(
            schedule.next_sequence, first_sequence_on_or_after(schedule, today)
        )
    _complete_if_over(schedule, today)
    await session.flush()
    return schedule


async def end_schedule(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    *,
    reason: str = "other",
    ended_at: Optional[_date] = None,
    today: Optional[_date] = None,
) -> InvoiceSchedule:
    """Terminal. The one decision an invoice cannot record: churn has a
    date and a side."""
    today = today or _today()
    if reason not in SCHEDULE_END_REASONS:
        raise InvoiceError("invalid_end_reason", "Unknown end reason")
    # Not checked against `start_date`: cancelling an agreement that was
    # to begin next month is an ordinary thing to do.
    ended_at = ended_at or today
    if schedule.status == "ended":
        return schedule
    schedule.status = "ended"
    schedule.pause_reason = None
    schedule.ended_at = ended_at
    schedule.end_reason = reason
    await session.flush()
    return schedule


async def delete_schedule(session: AsyncSession, schedule: InvoiceSchedule) -> None:
    """Only an agreement nothing was ever billed under. Anything else
    is ended, so the invoices keep the record of what they were for."""
    latest = await _latest_linked_period_start(session, schedule.id)
    if latest is not None:
        raise InvoiceError(
            "schedule_has_invoices", "Invoices were issued under this agreement; end it instead"
        )
    await session.delete(schedule)
    await session.flush()


# ---------------------------------------------------------------------------
# Linking invoices
# ---------------------------------------------------------------------------
async def link_invoice(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    invoice: Invoice,
    period_start_date: _date,
) -> Invoice:
    """Say that an existing invoice answers for a period of this agreement.

    The retroactive door: someone who billed a retainer by hand from
    March to August and only now created the schedule gets their
    history, and a Stripe import lands its invoices the same way. The
    date has to be a real period boundary, the period has to be free,
    and the invoice has to be the same money (currency, and client when
    both say one).
    """
    if invoice.workspace_id != schedule.workspace_id:
        raise InvoiceError("invoice_not_found", "Invoice not found", 404)
    # An agreement bills a client. A bill received is the supplier's
    # agreement, and summing it here would count money going out as
    # money coming in.
    if invoice.direction != "receivable":
        raise InvoiceError("not_receivable", "Only an invoice you issued can belong to an agreement")
    if invoice.schedule_id is not None:
        raise InvoiceError("already_linked", "This invoice already belongs to an agreement", 409)
    if invoice.status == "void":
        raise InvoiceError("invoice_void", "A voided invoice cannot answer for a period")
    if invoice.currency != schedule.currency:
        raise InvoiceError("currency_mismatch", "The invoice and the agreement are in different currencies")
    if invoice.payee_id and schedule.payee_id and invoice.payee_id != schedule.payee_id:
        raise InvoiceError("payee_mismatch", "The invoice and the agreement name different clients")

    sequence = sequence_for_period_start(schedule, period_start_date)
    if sequence is None:
        raise InvoiceError("not_a_period", "That date is not the start of a period of this agreement")
    if not period_within_end(schedule, sequence):
        raise InvoiceError("beyond_end", "That period is after the agreement ends")
    taken = await session.execute(
        select(Invoice.id).where(Invoice.schedule_id == schedule.id, Invoice.sequence == sequence)
    )
    if taken.scalar_one_or_none() is not None:
        raise InvoiceError("period_taken", "Another invoice already answers for that period", 409)

    start, end = period_bounds(schedule, sequence)
    invoice.schedule_id = schedule.id
    invoice.sequence = sequence
    invoice.period_start = start
    invoice.period_end = end
    # A period billed by hand before the job got to it is not billed
    # again by the job.
    if sequence >= schedule.next_sequence:
        schedule.next_sequence = sequence + 1
    await session.flush()
    return invoice


async def unlink_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    """The invoice stays, the period is free again. The cursor is not
    moved back: the job does not re-bill a period a person unlinked."""
    invoice.schedule_id = None
    invoice.sequence = None
    invoice.period_start = None
    invoice.period_end = None
    await session.flush()
    return invoice


def _lines_from_invoice(invoice: Invoice, fallback_description: str) -> list[dict[str, Any]]:
    if invoice.lines:
        return [
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": line.unit_price,
                "tax_rate": line.tax_rate,
            }
            for line in invoice.lines
        ]
    # A three-field invoice has no lines; the agreement gets one that
    # says what the invoice said.
    return [{"description": fallback_description, "quantity": 1, "unit_price": invoice.total}]


async def make_recurring(
    session: AsyncSession,
    invoice: Invoice,
    user_id: Optional[uuid.UUID],
    data: dict[str, Any],
    today: Optional[_date] = None,
) -> InvoiceSchedule:
    """Turn an invoice into the first period of a new agreement.

    The natural door: the invoice for September is already written, and
    "repeat this monthly" is the whole request. Everything the schedule
    needs is on the invoice; the caller adds a frequency and, if they
    like, a name and an end.
    """
    today = today or _today()
    if invoice.status in ("draft", "void"):
        raise InvoiceError(
            "not_issued", "Only an issued invoice can start an agreement"
        )
    if invoice.direction != "receivable":
        raise InvoiceError("not_receivable", "Only an invoice you issued can start an agreement")
    if invoice.schedule_id is not None:
        raise InvoiceError("already_linked", "This invoice already belongs to an agreement", 409)

    name = (data.get("name") or "").strip() or (
        invoice.lines[0].description if invoice.lines else (invoice.payee.name if invoice.payee else "")
    )
    if not name:
        raise InvoiceError("name_required", "Give the agreement a name")

    # The anchor defaults to the invoice's own date, but may be earlier:
    # "this has been going since March" makes March period one and the
    # invoice in hand a later period, so the months between can be
    # linked. Whatever the anchor, the invoice has to land on a period
    # boundary, or it would not be a period of the agreement it starts.
    frequency = data.get("frequency")
    if frequency not in SCHEDULE_FREQUENCIES:
        raise InvoiceError("invalid_frequency", "Unknown frequency")
    start_date = data.get("start_date") or invoice.issue_date
    if start_date > invoice.issue_date:
        raise InvoiceError("start_after_invoice", "The agreement cannot start after this invoice")
    probe = InvoiceSchedule(frequency=frequency, start_date=start_date)
    if sequence_for_period_start(probe, invoice.issue_date) is None:
        raise InvoiceError(
            "not_a_period", "With that start date, this invoice does not fall on a period boundary"
        )

    schedule = await create_schedule(
        session,
        invoice.workspace_id,
        user_id,
        {
            "name": name,
            "payee_id": invoice.payee_id,
            "frequency": frequency,
            "start_date": start_date,
            "end_type": data.get("end_type") or "never",
            "end_date": data.get("end_date"),
            "end_count": data.get("end_count"),
            "payment_terms_days": (
                data["payment_terms_days"]
                if data.get("payment_terms_days") is not None
                else (invoice.due_date - invoice.issue_date).days
            ),
            "currency": invoice.currency,
            "notes": invoice.notes,
            "custom_fields": invoice.custom_fields,
            "lines": _lines_from_invoice(invoice, name),
            "discount": invoice.discount if invoice.lines else ZERO,
        },
        today=today,
    )
    await link_invoice(session, schedule, invoice, invoice.issue_date)
    return schedule


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
async def _payment_terms(session: AsyncSession, schedule: InvoiceSchedule) -> int:
    if schedule.payment_terms_days is not None:
        return schedule.payment_terms_days
    settings = await invoice_service.get_settings(session, schedule.workspace_id)
    return settings.default_payment_terms_days


async def _emit(
    session: AsyncSession, schedule: InvoiceSchedule, sequence: int
) -> Invoice:
    """One invoice for one period, priced by the term in force."""
    start, end = period_bounds(schedule, sequence)
    term = term_for(schedule, start)
    if term is None:
        raise InvoiceError("no_term", "No term is in force for this period")
    terms_days = await _payment_terms(session, schedule)
    if schedule.user_id is None:
        # The ledger stamps who created each invoice. An agreement whose
        # author left the workspace keeps emitting, and the invoice is
        # then authored by nobody in particular rather than not at all.
        raise InvoiceError("no_author", "This agreement has no author to issue invoices as")
    invoice = await invoice_service.create_invoice(
        session,
        schedule.workspace_id,
        schedule.user_id,
        {
            "payee_id": schedule.payee_id,
            "issue_date": start,
            "due_date": start + timedelta(days=terms_days),
            "competence_date": start,
            "currency": schedule.currency,
            "discount": Decimal(term.discount),
            "lines": [dict(line) for line in term.lines],
            "notes": schedule.notes,
            "custom_fields": schedule.custom_fields,
        },
    )
    invoice.schedule_id = schedule.id
    invoice.sequence = sequence
    invoice.period_start = start
    invoice.period_end = end
    await session.flush()
    return invoice


async def generate_due(
    session: AsyncSession,
    schedule: InvoiceSchedule,
    today: Optional[_date] = None,
    *,
    force_next: bool = False,
) -> list[Invoice]:
    """Emit every period whose start has arrived, oldest first.

    Idempotent by construction: a period already answered for (by the
    job or by a hand-linked invoice) is skipped and the cursor moves
    past it. `force_next` emits the next period whether or not its date
    has come, for "bill this now"; it never emits more than one.

    A failure counts against the schedule, and the third in a row
    pauses it. The job's own transaction handling decides whether the
    failure is committed; this function only records it.
    """
    today = today or _today()
    if schedule.status != "active" or schedule.origin != "local":
        return []
    # Two runs on one agreement (a double click on "issue next now", or
    # the button racing the hourly job) would both read the same cursor.
    # Locking the row makes the second wait, then read the cursor the
    # first one moved, so it finds nothing left to emit. Columns rather
    # than the entity: the entity joins the payee, and Postgres refuses
    # FOR UPDATE on the nullable side of an outer join.
    locked = (
        await session.execute(
            select(InvoiceSchedule.status, InvoiceSchedule.next_sequence)
            .where(InvoiceSchedule.id == schedule.id)
            .with_for_update()
        )
    ).one()
    if locked.status != "active":
        return []
    # The instance may predate the lock: a worker loads it, then a PATCH
    # moves the start date and recomputes the cursor, then the worker
    # gets here. Emitting from the stale calendar would skip periods.
    # So everything the emission reads is reloaded now that the row is
    # ours. Only those fields: the failure count lives on the instance
    # until the caller commits it, and reloading it would reset it.
    await session.refresh(
        schedule,
        attribute_names=[
            "status", "frequency", "start_date", "end_type", "end_date",
            "end_count", "next_sequence", "payee_id", "currency",
            "payment_terms_days", "notes", "custom_fields", "terms",
        ],
    )
    emitted: list[Invoice] = []
    try:
        while len(emitted) < MAX_PERIODS_PER_RUN:
            if _complete_if_over(schedule, today):
                break
            sequence = schedule.next_sequence
            if not force_next and period_start(schedule, sequence) > today:
                break
            already = await session.execute(
                select(Invoice.id).where(
                    Invoice.schedule_id == schedule.id, Invoice.sequence == sequence
                )
            )
            if already.scalar_one_or_none() is None:
                emitted.append(await _emit(session, schedule, sequence))
                schedule.last_generated_at = datetime.now(timezone.utc)
            schedule.next_sequence = sequence + 1
            if force_next:
                break
        schedule.consecutive_failures = 0
        _complete_if_over(schedule, today)
    except IntegrityError:
        # The unique (schedule, sequence) caught a period another run
        # emitted first. The period exists, so this is not a failure and
        # must not count towards pausing the agreement.
        await session.rollback()
        raise InvoiceError(
            "period_already_issued",
            "This period was issued by another run a moment ago",
            status_code=409,
        ) from None
    except Exception:
        schedule.consecutive_failures += 1
        if schedule.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            schedule.status = "paused"
            schedule.pause_reason = "failures"
        raise
    await session.flush()
    return emitted


async def after_generation(
    session: AsyncSession, invoice: Invoice, workspace: Workspace
) -> None:
    """What the create route does after an invoice is issued, for an
    invoice the job issued: file the document, and look back at money
    that may already have arrived for it. Both are best effort."""
    try:
        await invoice_archive.file_issued_document(session, invoice, workspace, invoice.user_id)
        await session.commit()
    except Exception:
        logger.exception("Could not file the document for invoice %s", invoice.id)
        await session.rollback()
    try:
        await reconciliation_service.match_for_invoice(session, invoice)
        await session.commit()
    except Exception:
        logger.exception("Could not look back for payments for invoice %s", invoice.id)
        await session.rollback()


async def due_schedule_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Every active local agreement, across workspaces. The date test is
    done per schedule in `generate_due`, because a period start is a
    walk from the anchor and not a column to compare in SQL."""
    result = await session.execute(
        select(InvoiceSchedule.id).where(
            InvoiceSchedule.status == "active", InvoiceSchedule.origin == "local"
        )
    )
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectedPeriod:
    """A period not yet invoiced, as the forecast will carry it."""

    due_date: _date
    currency: str
    amount: Decimal


async def projected_periods(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    range_start: _date,
    range_end: _date,
) -> list[ProjectedPeriod]:
    """Periods of active agreements falling due in `[range_start, range_end)`
    that no invoice answers for yet.

    Only from the cursor onwards: everything before it is either an
    invoice (the forecast already carries it as one) or was skipped on
    purpose. A period at the cursor whose start is already past is still
    projected, dated by its due date, because the job will emit it on
    its next run and the money is still expected.
    """
    schedules = [
        s
        for s in await list_schedules(session, workspace_id, status="active")
        if s.origin == "local"
    ]
    if not schedules:
        return []
    settings: Optional[InvoiceSettings] = None
    out: list[ProjectedPeriod] = []
    for schedule in schedules:
        if schedule.payment_terms_days is None:
            settings = settings or await invoice_service.get_settings(session, workspace_id)
            terms_days = settings.default_payment_terms_days
        else:
            terms_days = schedule.payment_terms_days
        sequence = schedule.next_sequence
        steps = 0
        while steps < _MAX_WALK and period_within_end(schedule, sequence):
            start = period_start(schedule, sequence)
            due = start + timedelta(days=terms_days)
            if due >= range_end:
                break
            if due >= range_start:
                term = term_for(schedule, start)
                if term is not None and term.total > ZERO:
                    out.append(ProjectedPeriod(due, schedule.currency, Decimal(term.total)))
            sequence += 1
            steps += 1
    return out
