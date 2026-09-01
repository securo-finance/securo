"""Invoice ledger: issuing, settling, and reading what is owed.

The one rule this module exists to enforce: **stored status is a
decision, derived status is a fact**. Four stored values (`draft`,
`open`, `void`, `uncollectible`) record what a person did. Everything a
reader actually wants to know — is it paid, partly paid, late, how late
— is computed here, from allocations and the due date, every time it is
asked for.

The cost of the alternative is visible in every peer product that stores
`overdue`: a nightly job to set it, compensating SQL to unset it when a
due date moves, and a permanent tax on every query downstream that has
to remember the state is really two states.
"""
import secrets
import uuid
from datetime import date as _date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.fiscal.registry import TaxIdKind, normalise_and_validate
from app.models.invoice import (
    MANUAL_METHOD,
    Invoice,
    InvoiceAllocation,
    InvoiceLine,
    InvoiceSettings,
)
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceTaxId
# Safe at module level: the attachment service reaches for models and the
# storage provider, never back into this one.
from app.services import invoice_attachment_service

ZERO = Decimal("0.00")


class InvoiceError(Exception):
    """A rule of the ledger was broken.

    Carries a stable `code` so the API layer maps it to a status without
    matching on prose, and the frontend translates it without parsing
    English.
    """

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
#: What each preset fills in. The preset is a starting point, not a mode:
#: after it is applied every field is individually overridable, and the
#: stored row is what the rest of the code reads.
PRESETS: dict[str, dict[str, Any]] = {
    # The NFS-e was issued at the prefeitura, the EU invoice will be
    # issued elsewhere, or there is no document at all — the user only
    # wants the money tracked.
    "tracking": {"document_required": False, "initial_state": "open", "tax_fields": "hidden"},
    # The invoice *is* the deliverable. Starts as a draft because a
    # document is written before it is sent, and an EU B2B invoice
    # without discriminated VAT is not a valid invoice.
    "document": {"document_required": True, "initial_state": "draft", "tax_fields": "optional"},
}


async def _settings_for_update(
    session: AsyncSession, workspace_id: uuid.UUID
) -> InvoiceSettings:
    """The settings row, locked for the length of the transaction.

    Numbering has to be allocated by the database, not by application
    code: two invoices issued at the same instant would otherwise read
    the same `next_number` and one of them would die on the unique
    constraint. The row lock makes the second wait rather than fail.

    `with_for_update` renders nothing on SQLite, which the test suite
    uses. That is acceptable because the guarantee is still there in
    production, and the unique constraint remains the backstop in both.
    """
    await get_settings(session, workspace_id)  # ensure the row exists
    result = await session.execute(
        select(InvoiceSettings)
        .where(InvoiceSettings.workspace_id == workspace_id)
        .with_for_update()
    )
    return result.scalar_one()


async def get_settings(session: AsyncSession, workspace_id: uuid.UUID) -> InvoiceSettings:
    """This workspace's settings, creating the default row on first read.

    Lazily created rather than seeded at workspace creation: a personal
    workspace never opens this module, and a row it will never read is a
    row that still has to be migrated forever.
    """
    result = await session.execute(
        select(InvoiceSettings).where(InvoiceSettings.workspace_id == workspace_id)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    settings = InvoiceSettings(workspace_id=workspace_id, preset="tracking", **PRESETS["tracking"])
    session.add(settings)
    await session.flush()
    return settings


#: Fields a caller may blank out deliberately. Everything else ignores a
#: null, so a partial update never wipes what it did not mention.
_NULLABLE_SETTINGS = (
    "issuer_display_name", "footer_note", "series", "number_prefix",
    "payment_details", "accent_color",
)


async def update_settings(
    session: AsyncSession, workspace_id: uuid.UUID, data: dict[str, Any]
) -> InvoiceSettings:
    settings = await get_settings(session, workspace_id)

    # A preset change refills the three fields it owns, then any explicit
    # values in the same request win over it — so "switch to document but
    # keep tax hidden" is one call, not two.
    preset = data.get("preset")
    if preset and preset != settings.preset:
        for key, value in PRESETS[preset].items():
            setattr(settings, key, value)

    for key, value in data.items():
        if value is not None or key in _NULLABLE_SETTINGS:
            setattr(settings, key, value)

    await session.flush()
    return settings


# ---------------------------------------------------------------------------
# Issuer identity — the workspace describing itself
# ---------------------------------------------------------------------------
async def get_issuer_tax_ids(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceTaxId]:
    result = await session.execute(
        select(WorkspaceTaxId)
        .where(WorkspaceTaxId.workspace_id == workspace_id)
        .order_by(WorkspaceTaxId.created_at)
    )
    return list(result.scalars().all())


async def set_issuer_tax_ids(
    session: AsyncSession, workspace_id: uuid.UUID, incoming: list[dict[str, Any]]
) -> list[WorkspaceTaxId]:
    """Replace this workspace's fiscal documents with `incoming`.

    Replace rather than merge, exactly as the payee side does: the caller
    sends the set that should remain, which makes removing a document the
    same operation as changing one. Validation goes through the same named
    validators — one implementation, asserted from both call sites.
    """
    normalised: dict[TaxIdKind, str] = {}
    for item in incoming:
        kind = TaxIdKind(item["kind"])
        value, error = normalise_and_validate(kind, item["value"])
        # An emptied field means "drop this document", not "store nothing".
        if error == "empty":
            continue
        if error:
            raise InvoiceError(
                f"invalid_tax_id:{kind.value}:{error}",
                f"That does not look like a valid {kind.value.upper()}",
            )
        normalised[kind] = value

    existing = {row.kind: row for row in await get_issuer_tax_ids(session, workspace_id)}
    for kind_value, row in existing.items():
        if TaxIdKind(kind_value) not in normalised:
            await session.delete(row)
    for kind, value in normalised.items():
        row = existing.get(kind.value)
        if row is not None:
            row.value = value
        else:
            session.add(
                WorkspaceTaxId(workspace_id=workspace_id, kind=kind.value, value=value)
            )
    await session.flush()
    return await get_issuer_tax_ids(session, workspace_id)


# ---------------------------------------------------------------------------
# Derived state — the whole point
# ---------------------------------------------------------------------------
def allocated_total(invoice: Invoice) -> Decimal:
    return sum((a.amount for a in invoice.allocations), ZERO)


def balance(invoice: Invoice) -> Decimal:
    return (invoice.total or ZERO) - allocated_total(invoice)


#: What a reader sees: the three terminal decisions, plus the four facts
#: computed from allocations and the due date. Mirrors `InvoiceState` in
#: `schemas/invoice.py`, which is what the API actually returns.
DerivedState = Literal[
    "draft", "open", "partial", "paid", "overdue", "void", "uncollectible"
]


def derive_state(invoice: Invoice, today: Optional[_date] = None) -> DerivedState:
    """What a reader wants to know, computed rather than stored.

    Returns one of: `draft`, `void`, `uncollectible`, `paid`, `partial`,
    `overdue`, `open`. The first three are the stored decisions passing
    straight through; the rest are facts about money and time.

    Order matters. A decision always wins over a fact — a voided invoice
    is not overdue, and an uncollectible one is not "partial" just
    because something was received against it before the client went
    under.
    """
    if invoice.status == "draft":
        return "draft"
    if invoice.status == "void":
        return "void"
    if invoice.status == "uncollectible":
        return "uncollectible"

    remaining = balance(invoice)
    if remaining <= ZERO:
        return "paid"

    reference = today or datetime.now(timezone.utc).date()
    if invoice.due_date and invoice.due_date < reference:
        return "overdue"
    return "partial" if allocated_total(invoice) > ZERO else "open"


def days_overdue(invoice: Invoice, today: Optional[_date] = None) -> int:
    reference = today or datetime.now(timezone.utc).date()
    if derive_state(invoice, reference) != "overdue":
        return 0
    return (reference - invoice.due_date).days


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
#: The side of the ledger everything currently written belongs to.
#: Every read narrows to one side: leaving it out would show a supplier's
#: bill inside "what clients owe me", which is not a filtering nicety but
#: a wrong number.
DEFAULT_DIRECTION = "receivable"


def _base_query(direction: str = DEFAULT_DIRECTION) -> Select:
    return (
        select(Invoice)
        .where(Invoice.direction == direction)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.allocations),
        )
    )


async def find_by_external_id(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    external_source: Optional[str],
    external_id: Optional[str],
) -> Optional[Invoice]:
    """The row that already holds this external document, if any.

    The lookup an importer does before writing, and the one the duplicate
    handler does after the database refuses. Both sides need it, which is
    why it is not buried in either.
    """
    if not external_source or not external_id:
        return None
    result = await session.execute(
        select(Invoice).where(
            Invoice.workspace_id == workspace_id,
            Invoice.external_source == external_source,
            Invoice.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def get_invoice(
    session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Invoice]:
    # Not narrowed by direction: an id already identifies one row, and
    # scoping here would 404 a payable a caller legitimately asked for.
    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.lines), selectinload(Invoice.allocations))
        .where(Invoice.id == invoice_id, Invoice.workspace_id == workspace_id)
    )
    return result.unique().scalar_one_or_none()


async def list_invoices(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    state: Optional[str] = None,
    year: Optional[int] = None,
    direction: str = DEFAULT_DIRECTION,
    payee_id: Optional[uuid.UUID] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Invoice]:
    """Invoices, newest first, optionally filtered by *derived* state.

    The state filter is applied in Python rather than SQL. That is a
    deliberate trade for this slice: the derived states depend on summed
    allocations, and one honest pass over a workspace's invoices beats a
    correlated subquery that would have to be kept in sync with
    `derive_state` by hand. When a workspace grows past a few thousand
    invoices this becomes a materialized balance column — and the single
    definition here is what makes that change safe.
    """
    query = _base_query(direction).where(
        Invoice.workspace_id == workspace_id,
        Invoice.document_type == "invoice",
    )
    query = _apply_year(query, year)
    if payee_id:
        query = query.where(Invoice.payee_id == payee_id)
    if q:
        pattern = f"%{q.lower()}%"
        query = query.where(func.lower(Invoice.notes).like(pattern))

    query = query.order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
    result = await session.execute(query)
    invoices = list(result.unique().scalars().all())

    if state:
        invoices = [inv for inv in invoices if derive_state(inv) == state]
    return invoices[offset : offset + limit]


def _apply_year(query: Select, year: Optional[int]) -> Select:
    """Narrow to one calendar year, by issue date.

    Issue date rather than competence: "the 2025 invoices" means the ones
    issued in 2025, which is what a person scanning a list is asking for.
    The accrual date is the accountant's axis and belongs in a report,
    not in a browse filter.
    """
    if year is None:
        return query
    return query.where(
        Invoice.issue_date >= _date(year, 1, 1),
        Invoice.issue_date <= _date(year, 12, 31),
    )


#: What the filter bar offers, and what each one counts. `overdue` is a
#: subset of `open` on purpose — these are filters, not a partition, and
#: a freelancer wants to jump straight to the late ones.
FACET_STATES: dict[str, tuple[str, ...]] = {
    "all": (),
    "open": ("open", "partial", "overdue"),
    "overdue": ("overdue",),
    "paid": ("paid",),
    "draft": ("draft",),
}


async def facets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    year: Optional[int] = None,
    direction: str = DEFAULT_DIRECTION,
) -> dict[str, Any]:
    """What the filter bar needs: which years exist, and how many in each state.

    One call rather than one per chip. The counts come from the same
    derived state the list uses, so a chip that says 3 opens a list of
    exactly 3 — a count computed a second way is a count that eventually
    disagrees.
    """
    years_result = await session.execute(
        select(func.extract("year", Invoice.issue_date))
        .where(
            Invoice.workspace_id == workspace_id,
            Invoice.document_type == "invoice",
            Invoice.direction == direction,
        )
        .distinct()
    )
    years = sorted((int(row[0]) for row in years_result.all()), reverse=True)

    result = await session.execute(
        _apply_year(
            _base_query(direction).where(
                Invoice.workspace_id == workspace_id,
                Invoice.document_type == "invoice",
            ),
            year,
        )
    )
    invoices = list(result.unique().scalars().all())
    states = [derive_state(invoice) for invoice in invoices]

    counts = {
        facet: len(invoices) if not wanted else sum(1 for s in states if s in wanted)
        for facet, wanted in FACET_STATES.items()
    }
    return {"years": years, "counts": counts}


async def aging_summary(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    today: Optional[_date] = None,
    direction: str = DEFAULT_DIRECTION,
) -> dict[str, Any]:
    """A receber · vencidas · recebido no mês · próximos vencimentos.

    Drafts are excluded from every figure — a document nobody has issued
    is not money anybody owes. Voided and uncollectible invoices are
    excluded for the same reason, one decision later.
    """
    reference = today or datetime.now(timezone.utc).date()
    result = await session.execute(
        _base_query(direction).where(
            Invoice.workspace_id == workspace_id,
            Invoice.document_type == "invoice",
            Invoice.status == "open",
        )
    )
    invoices = list(result.unique().scalars().all())

    outstanding = ZERO
    overdue_amount = ZERO
    overdue_count = 0
    upcoming: list[Invoice] = []
    # Aging buckets in days past due, the shape every accountant expects.
    buckets = {"current": ZERO, "d1_30": ZERO, "d31_60": ZERO, "d61_90": ZERO, "d90_plus": ZERO}

    for invoice in invoices:
        remaining = balance(invoice)
        if remaining <= ZERO:
            continue
        outstanding += remaining
        overdue_days = days_overdue(invoice, reference)
        if overdue_days > 0:
            overdue_amount += remaining
            overdue_count += 1
            if overdue_days <= 30:
                buckets["d1_30"] += remaining
            elif overdue_days <= 60:
                buckets["d31_60"] += remaining
            elif overdue_days <= 90:
                buckets["d61_90"] += remaining
            else:
                buckets["d90_plus"] += remaining
        else:
            buckets["current"] += remaining
            upcoming.append(invoice)

    # Money that actually arrived this month, by the date it arrived —
    # the cash view. The accrual view reads `competence_date` instead,
    # which is why both are stored.
    month_start = reference.replace(day=1)
    received_this_month = ZERO
    for invoice in invoices:
        for allocation in invoice.allocations:
            allocated_on = allocation.allocated_at.date()
            if month_start <= allocated_on <= reference:
                received_this_month += allocation.amount

    upcoming.sort(key=lambda i: i.due_date)
    return {
        "outstanding": outstanding,
        "overdue_amount": overdue_amount,
        "overdue_count": overdue_count,
        "received_this_month": received_this_month,
        "buckets": buckets,
        "upcoming": upcoming[:5],
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _line_total(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return (Decimal(quantity) * Decimal(unit_price)).quantize(Decimal("0.01"))


def _recompute_totals(invoice: Invoice) -> None:
    """Totals follow the lines when there are lines, and the caller when
    there are not.

    Under the `tracking` preset an invoice is three fields — "Fulano me
    deve R$3.000" — and its total is simply what the user typed. Once
    lines exist they are the source of truth, because two places holding
    the same number is how they end up disagreeing.
    """
    if not invoice.lines:
        return
    subtotal = sum((line.total for line in invoice.lines), ZERO)
    tax_total = sum(
        (
            (line.total * (line.tax_rate or ZERO) / Decimal("100")).quantize(Decimal("0.01"))
            for line in invoice.lines
        ),
        ZERO,
    )
    invoice.subtotal = subtotal
    invoice.tax_total = tax_total
    total = subtotal - (invoice.discount or ZERO) + tax_total
    # Refused here rather than left to the CHECK. A discount larger than
    # what is being billed is a typo, and the database answering it with
    # an IntegrityError turns a correctable mistake into a 500 the client
    # cannot branch on.
    if total < ZERO:
        raise InvoiceError(
            "discount_exceeds_total",
            "The discount is larger than the amount being billed",
        )
    invoice.total = total


async def _assert_payee(
    session: AsyncSession, payee_id: Optional[uuid.UUID], workspace_id: uuid.UUID
) -> Optional[Payee]:
    if payee_id is None:
        return None
    result = await session.execute(
        select(Payee).where(Payee.id == payee_id, Payee.workspace_id == workspace_id)
    )
    payee = result.scalar_one_or_none()
    if payee is None:
        raise InvoiceError("payee_not_found", "Payee not found in this workspace", 404)
    return payee


def _build_line(invoice: Invoice, line: dict[str, Any], position: int) -> InvoiceLine:
    quantity = Decimal(str(line.get("quantity", 1)))
    unit_price = Decimal(str(line.get("unit_price", 0)))
    return InvoiceLine(
        invoice_id=invoice.id,
        workspace_id=invoice.workspace_id,
        description=line["description"],
        quantity=quantity,
        unit=(line.get("unit") or None),
        unit_price=unit_price,
        tax_rate=Decimal(str(line["tax_rate"])) if line.get("tax_rate") is not None else None,
        total=_line_total(quantity, unit_price),
        position=position,
    )


async def create_invoice(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: dict[str, Any],
) -> Invoice:
    settings = await get_settings(session, workspace_id)
    await _assert_payee(session, data.get("payee_id"), workspace_id)

    lines_data = data.pop("lines", None) or []
    if settings.document_required and not lines_data:
        raise InvoiceError(
            "lines_required", "This workspace requires invoices to carry line items"
        )

    # Provenance is declared, never inferred: a row that says `imported`
    # has to say what it was imported from, or the pair that makes a
    # re-sync converge on one row is missing and the second sync creates
    # a duplicate instead of updating.
    origin = data.get("origin") or "local"
    if origin == "imported" and not data.get("external_source"):
        raise InvoiceError(
            "external_source_required",
            "An imported document must say where it came from",
        )

    issue_date = data.get("issue_date") or datetime.now(timezone.utc).date()
    due_date = data.get("due_date")
    if due_date is None:
        due_date = issue_date + timedelta(days=settings.default_payment_terms_days)
    if due_date < issue_date:
        raise InvoiceError("due_before_issue", "Due date cannot precede the issue date")

    invoice = Invoice(
        workspace_id=workspace_id,
        user_id=user_id,
        payee_id=data.get("payee_id"),
        issue_date=issue_date,
        due_date=due_date,
        # Defaults to the issue date, and stays its own field so an
        # accrual-basis report can disagree with the invoice date.
        competence_date=data.get("competence_date") or issue_date,
        currency=data.get("currency") or "USD",
        discount=data.get("discount") or ZERO,
        subtotal=data.get("subtotal") or ZERO,
        tax_total=data.get("tax_total") or ZERO,
        total=data.get("total") or ZERO,
        direction=data.get("direction") or DEFAULT_DIRECTION,
        origin=origin,
        external_source=data.get("external_source"),
        external_id=data.get("external_id"),
        # Only an import has one. On a local document our own counter
        # names it, and a second name would be a second answer to the
        # same question.
        external_number=(data.get("external_number") if origin == "imported" else None),
        notes=data.get("notes"),
        internal_notes=data.get("internal_notes"),
        custom_fields=data.get("custom_fields"),
        status="draft",
    )
    session.add(invoice)
    try:
        await session.flush()
    except IntegrityError:
        # The unique (workspace, source, external id) is what makes a
        # re-sync converge on one row instead of duplicating. Answer with
        # the row that already holds this document so a caller can update
        # it rather than guess, and so a retried import is a no-op rather
        # than a crash.
        await session.rollback()
        existing = await find_by_external_id(
            session, workspace_id, data.get("external_source"), data.get("external_id")
        )
        where = f" as {existing.number}" if existing and existing.number else ""
        raise InvoiceError(
            "already_imported",
            f"This document is already here{where}",
            status_code=409,
        ) from None

    for position, line in enumerate(lines_data):
        session.add(_build_line(invoice, line, position))
    await session.flush()
    await session.refresh(invoice, ["lines", "allocations", "payee"])
    _recompute_totals(invoice)

    # `open` on creation is the tracking preset's whole point: the money
    # is already owed, and making the user press "issue" on a note to
    # self is ceremony.
    #
    # `as_draft` is the caller saying they are not finished — a document
    # half written, to be picked up later. It overrides the preset,
    # because the preset is a default about the common case and this is
    # someone stating the uncommon one.
    #
    # An import is exempt either way: somebody issued it elsewhere, and
    # `draft` would claim we are still writing a document we received.
    wants_draft = bool(data.get("as_draft"))
    if (settings.initial_state == "open" and not wants_draft) or invoice.origin == "imported":
        if (invoice.total or ZERO) <= ZERO:
            raise InvoiceError("empty_total", "An invoice with no value cannot be issued")
        locked = await _settings_for_update(session, workspace_id)
        workspace = await session.get(Workspace, workspace_id)
        _issue(invoice, locked, workspace, await _snapshot_tax_ids(session, workspace_id))

    await session.flush()
    return invoice


#: Financial substance. Editable while a draft, frozen once issued: the
#: document has left the building, and a total that changes after the
#: client received it is not an edit, it is a second document.
_DRAFT_ONLY_FIELDS = (
    "payee_id", "issue_date", "due_date", "competence_date", "currency",
    "discount", "subtotal", "tax_total", "total",
)
#: The seller's own record, editable at any time.
_ALWAYS_EDITABLE = ("notes", "internal_notes", "custom_fields")


async def update_invoice(session: AsyncSession, invoice: Invoice, data: dict[str, Any]) -> Invoice:
    if invoice.status in ("void", "uncollectible"):
        raise InvoiceError("terminal_status", f"A {invoice.status} invoice cannot be edited")

    if invoice.status == "draft":
        if data.get("payee_id") is not None:
            await _assert_payee(session, data["payee_id"], invoice.workspace_id)
        lines_data = data.pop("lines", None)
        for field in _DRAFT_ONLY_FIELDS:
            if data.get(field) is not None:
                setattr(invoice, field, data[field])

        if lines_data is not None:
            for line in list(invoice.lines):
                await session.delete(line)
            await session.flush()
            await session.refresh(invoice, ["lines"])
            for position, line in enumerate(lines_data):
                session.add(_build_line(invoice, line, position))
            await session.flush()
            await session.refresh(invoice, ["lines"])
            _recompute_totals(invoice)

        if invoice.due_date < invoice.issue_date:
            raise InvoiceError("due_before_issue", "Due date cannot precede the issue date")
    else:
        rejected = {k for k in data if k in _DRAFT_ONLY_FIELDS and data[k] is not None}
        if rejected or data.get("lines") is not None:
            raise InvoiceError(
                "issued_invoice_immutable",
                "An issued invoice's financial fields cannot change — void it and issue a new one",
            )

    for field in _ALWAYS_EDITABLE:
        if field in data:
            setattr(invoice, field, data[field])

    await session.flush()
    return invoice


async def _snapshot_tax_ids(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[dict[str, str]]:
    """The issuer's tax documents, in the shape the snapshot stores.

    Read at issuance and frozen, for the same reason as the rest of the
    issuer block: correcting a CNPJ next month must not rewrite the CNPJ
    on an invoice a client received last month.
    """
    return [
        {"kind": row.kind, "value": row.value}
        for row in await get_issuer_tax_ids(session, workspace_id)
    ]


def _build_snapshot(
    invoice: Invoice,
    settings: InvoiceSettings,
    workspace: Optional[Workspace] = None,
    issuer_tax_ids: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Freeze what the document said about itself.

    Read at render time instead of the live settings and the live payee,
    so changing a logo in September leaves August's invoice exactly as
    the client received it.
    """
    payee = invoice.payee
    return {
        "issuer": {
            "display_name": settings.issuer_display_name,
            "legal_name": workspace.legal_name if workspace else None,
            "address": workspace.address if workspace else None,
            "tax_ids": issuer_tax_ids or [],
            "logo_id": str(settings.logo_id) if settings.logo_id else None,
            "footer_note": settings.footer_note,
            "payment_details": settings.payment_details,
            "accent_color": settings.accent_color,
        },
        "counterparty": {
            "name": payee.name if payee else None,
            "email": payee.email if payee else None,
            "address": payee.address if payee else None,
            # The client's documents as they stood on the day. A CNPJ
            # corrected next month must not appear on a document the
            # client already holds showing the old one.
            "tax_ids": [
                {"kind": t.kind, "value": t.value} for t in (payee.tax_ids if payee else [])
            ],
        },
        "template": settings.template or {},
        # The language the document was written in. Frozen with the rest:
        # a workspace that later switches its interface must not retitle
        # an invoice the client already holds.
        "locale": workspace.locale if workspace else None,
        "number_prefix": settings.number_prefix,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }


def _issue(
    invoice: Invoice,
    settings: InvoiceSettings,
    workspace: Optional[Workspace] = None,
    issuer_tax_ids: Optional[list[dict[str, str]]] = None,
) -> None:
    """Open the invoice, numbering and freezing it only if it is ours.

    **Numbering.** Our counter only ever moves forward, and a voided
    invoice keeps the number it consumed: reusing one would put two
    documents under a single identifier, which every jurisdiction that
    regulates numbering forbids and no other would thank us for.

    An **imported** document is exempt from all of that. It already has
    an identity — the source called it something — so taking a number
    from our sequence would burn one that is supposed to be gapless and
    rename a document its recipient already holds under another name. It
    keeps whatever number came with it, or none.

    **Snapshot.** Freezing our issuer identity is a record of what *we*
    put on a document at the moment we issued it. On an imported row
    somebody else issued it and we are keeping a record, so the same
    freeze would stamp our details onto a page we did not write and give
    it a date that means nothing. The attached file is that document; the
    snapshot is for ours.
    """
    local = invoice.origin == "local"
    if local:
        invoice.number = settings.next_number
        invoice.series = settings.series
        settings.next_number += 1
        invoice.snapshot = _build_snapshot(invoice, settings, workspace, issuer_tax_ids)
    invoice.status = "open"


async def issue_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    if invoice.status != "draft":
        raise InvoiceError("not_a_draft", "Only a draft can be issued")
    settings = await get_settings(session, invoice.workspace_id)
    if settings.document_required and not invoice.lines:
        raise InvoiceError("lines_required", "This workspace requires invoices to carry line items")
    if (invoice.total or ZERO) <= ZERO:
        raise InvoiceError("empty_total", "An invoice with no value cannot be issued")
    locked = await _settings_for_update(session, invoice.workspace_id)
    workspace = await session.get(Workspace, invoice.workspace_id)
    _issue(
        invoice, locked, workspace, await _snapshot_tax_ids(session, invoice.workspace_id)
    )
    await session.flush()
    return invoice


async def void_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    """Cancel an issued invoice, keeping its paper trail.

    Not a delete. The number stays taken, the row stays readable, and
    every total treats it as zero. Deleting it would leave a gap nobody
    could explain later — which is precisely what numbering rules exist
    to prevent.
    """
    if invoice.status == "draft":
        raise InvoiceError("draft_not_voidable", "Delete the draft instead of voiding it")
    if invoice.status == "void":
        return invoice
    if invoice.allocations:
        raise InvoiceError(
            "void_with_allocations", "Unlink the payments before voiding this invoice"
        )
    invoice.status = "void"
    await session.flush()
    return invoice


async def mark_uncollectible(session: AsyncSession, invoice: Invoice) -> Invoice:
    """Give up on collecting. The whole-invoice decision.

    Distinct from a partial write-off, which is a deduction and arrives
    with the matching work. The two must never coexist on one invoice:
    one decision, one mechanism.
    """
    if invoice.status != "open":
        raise InvoiceError("not_open", "Only an open invoice can be written off")
    invoice.status = "uncollectible"
    await session.flush()
    return invoice


async def reopen_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    """Undo an uncollectible decision — the client paid after all."""
    if invoice.status != "uncollectible":
        raise InvoiceError("not_uncollectible", "Only an uncollectible invoice can be reopened")
    invoice.status = "open"
    await session.flush()
    return invoice


async def delete_invoice(session: AsyncSession, invoice: Invoice) -> None:
    if invoice.status != "draft":
        raise InvoiceError(
            "only_drafts_deletable", "An issued invoice is never deleted — void it instead"
        )
    # The attachment rows go with the invoice through the cascade, but the
    # stored files have to be asked for by name — nothing in the database
    # reaches into the storage provider. Left out, every deleted draft
    # would leave its uploads behind forever.
    await invoice_attachment_service.cleanup_files(session, invoice.id)
    await session.delete(invoice)
    await session.flush()


# ---------------------------------------------------------------------------
# Allocations
# ---------------------------------------------------------------------------
async def allocate(
    session: AsyncSession,
    invoice: Invoice,
    transaction_id: uuid.UUID,
    amount: Optional[Decimal] = None,
    method: str = MANUAL_METHOD,
) -> InvoiceAllocation:
    """Bind money to debt. **This is the apply step, never the deciding one.**

    Automatic matching will decide in a separate, pure function that reads
    the reconciliation policy and returns a decision plus a trace; this is
    what it calls once a human or that decision has settled the question.
    Keeping the two apart is what lets the matcher be dry-run and previewed
    later, which a step that writes can never be.

    `method` records *who or what* decided: `manual`, or the id of the
    strategy that fired. The guards below run either way — an automatic
    decision is not a trusted one.
    """
    if invoice.status != "open":
        raise InvoiceError("not_open", "Only an open invoice can be settled")

    result = await session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == invoice.workspace_id,
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        # Same message whether it does not exist or belongs to another
        # workspace: the second case must not be distinguishable.
        raise InvoiceError("transaction_not_found", "Transaction not found in this workspace", 404)

    # Same-currency only in this slice, and it fails loudly rather than
    # silently binding a USD invoice to a BRL inflow at an implied rate
    # nobody chose. Multi-currency settlement is its own piece of work.
    if (transaction.currency or invoice.currency) != invoice.currency:
        raise InvoiceError(
            "currency_mismatch",
            f"This invoice is in {invoice.currency} and the transaction is in {transaction.currency}",
        )

    available = balance(invoice)
    if available <= ZERO:
        raise InvoiceError("already_settled", "This invoice is already fully settled")

    # Magnitude, not sign: a receivable is settled by money coming in and
    # a payable by money going out, and the allocation records how much of
    # that movement this document accounts for either way. Defaulting to
    # the smaller of "what is left" and "what moved" is what makes the
    # common case — one payment, one document — a single click.
    incoming = abs(transaction.amount)
    proposed = Decimal(amount) if amount is not None else min(available, incoming)
    if proposed <= ZERO:
        raise InvoiceError("amount_not_positive", "Allocation amount must be positive")
    if proposed > available:
        raise InvoiceError("over_allocation", f"Only {available} remains on this invoice")

    allocation = InvoiceAllocation(
        invoice_id=invoice.id,
        workspace_id=invoice.workspace_id,
        transaction_id=transaction_id,
        amount=proposed,
        method=method,
    )
    session.add(allocation)
    try:
        await session.flush()
    except IntegrityError:
        raise InvoiceError(
            "already_allocated", "This transaction is already linked to this invoice"
        )
    await session.refresh(invoice, ["allocations"])
    return allocation


async def unallocate(session: AsyncSession, invoice: Invoice, allocation_id: uuid.UUID) -> None:
    result = await session.execute(
        select(InvoiceAllocation).where(
            InvoiceAllocation.id == allocation_id,
            InvoiceAllocation.invoice_id == invoice.id,
        )
    )
    allocation = result.scalar_one_or_none()
    if allocation is None:
        raise InvoiceError("allocation_not_found", "Allocation not found", 404)
    await session.delete(allocation)
    await session.flush()
    await session.refresh(invoice, ["allocations"])


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------
async def create_share_token(session: AsyncSession, invoice: Invoice) -> str:
    """A link anyone holding it can open, and nothing more.

    Only issued invoices get one: a draft is not a document, and handing
    out a link to something still being written invites the client to
    read a number that is about to change.

    The token is the credential, so it is generated with
    `secrets.token_urlsafe` and never derived from the invoice id — a
    guessable link would expose every client's document to anyone who
    could count.
    """
    if invoice.status == "draft":
        raise InvoiceError("draft_not_shareable", "Issue the invoice before sharing it")
    # A payable is the supplier's document, not ours. There is nobody to
    # send it to, and publishing someone else's invoice on a public link
    # is a leak with no upside.
    if invoice.direction != DEFAULT_DIRECTION:
        raise InvoiceError(
            "payable_not_shareable", "A bill you received cannot be shared"
        )
    token = invoice.share_token
    if not token:
        # Bound to a local so the return type is a `str`, not the
        # `str | None` the column is: the branch above guarantees it.
        token = secrets.token_urlsafe(32)
        invoice.share_token = token
        await session.flush()
    return token


async def revoke_share_token(session: AsyncSession, invoice: Invoice) -> None:
    """Nulling the token is the revocation. Anyone holding the old link
    gets a 404, which is the same answer a link that never existed gets."""
    invoice.share_token = None
    await session.flush()


async def get_invoice_by_share_token(session: AsyncSession, token: str) -> Optional[Invoice]:
    """The public lookup. Scoped to nothing — the token *is* the scope."""
    if not token:
        return None
    result = await session.execute(
        _base_query().where(
            Invoice.share_token == token,
            # A voided document must stop being reachable: it was
            # cancelled, and a link that keeps serving it says otherwise.
            Invoice.status.in_(("open", "uncollectible")),
        )
    )
    return result.unique().scalar_one_or_none()


async def invoice_links_for_transactions(
    session: AsyncSession, workspace_id: uuid.UUID, transaction_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Which invoices each of these transactions settles, for the badge.

    A **list** per transaction, because one is the whole point of the
    N:N: a gateway payout settles a dozen invoices at once, net of fees.
    Keying a single link by transaction id let the second allocation
    overwrite the first, so a transaction that paid three invoices
    advertised one and the ledger and the list disagreed.

    One query for a page of transactions rather than one per row: the
    transaction list is the hottest screen in the product and this must
    never become an N+1.
    """
    if not transaction_ids:
        return {}
    result = await session.execute(
        select(InvoiceAllocation, Invoice)
        .join(Invoice, Invoice.id == InvoiceAllocation.invoice_id)
        .where(
            InvoiceAllocation.workspace_id == workspace_id,
            InvoiceAllocation.transaction_id.in_(transaction_ids),
        )
    )
    links: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for allocation, invoice in result.all():
        links.setdefault(allocation.transaction_id, []).append(
            {
                "invoice_id": invoice.id,
                "number": invoice.number,
                "series": invoice.series,
                # An imported invoice is named by the source, and without
                # this the badge for one would have nothing to show.
                "external_number": invoice.external_number,
                "amount": allocation.amount,
            }
        )
    return links
