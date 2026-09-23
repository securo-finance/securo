"""Recurring invoice routes.

Gated exactly like `/api/invoices`: `require_module(INVOICES)` for reads,
the write variant for anything that changes an agreement. A personal
workspace gets a 404 from all of them.

Mounted under its own prefix rather than `/api/invoices/schedules`,
because `/api/invoices/{invoice_id}` is declared with a UUID path
parameter and would answer `schedules` with a 422 before this router
was consulted.
"""
import uuid
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.invoices import _http, _serialize as _serialize_invoice
from app.core.database import get_async_session
from app.core.module_gate import require_module, require_module_write
from app.core.workspace_context import WorkspaceContext
from app.models.invoice import Invoice
from app.models.invoice_schedule import InvoiceSchedule
from app.schemas.invoice import InvoiceRead
from app.schemas.invoice_schedule import (
    ScheduleCreate,
    ScheduleEnd,
    ScheduleLinkInvoice,
    SchedulePeriodRead,
    ScheduleRead,
    ScheduleSummaryRead,
    ScheduleTermInput,
    ScheduleTermRead,
    ScheduleTermUpdate,
    ScheduleUpdate,
)
from app.services import invoice_schedule_service as svc
from app.services import invoice_service
from app.services.invoice_service import InvoiceError
from app.services.module_service import ModuleId

router = APIRouter(prefix="/api/invoice-schedules", tags=["invoice-schedules"])

read_ctx = require_module(ModuleId.INVOICES)
write_ctx = require_module_write(ModuleId.INVOICES)


def _serialize(
    schedule: InvoiceSchedule,
    figures: Optional[svc.ScheduleFigures] = None,
    today: Optional[_date] = None,
) -> ScheduleRead:
    """Attach the derived answers to the stored row. The only place that
    builds a `ScheduleRead`, so every figure comes from one definition."""
    payload = ScheduleRead.model_validate(schedule, from_attributes=True)
    today = today or svc._today()
    current = svc.term_for(schedule, today)
    payload.current_term = ScheduleTermRead.model_validate(current) if current else None
    upcoming = svc.next_period_start(schedule)
    payload.next_period_start = upcoming
    next_term = svc.term_for(schedule, upcoming) if upcoming else current
    payload.next_term = ScheduleTermRead.model_validate(next_term) if next_term else None
    payload.monthly_amount = svc.monthly_amount(schedule, current)
    if figures is not None:
        payload.invoice_count = figures.invoice_count
        payload.amount_invoiced = figures.amount_invoiced
        payload.amount_paid = figures.amount_paid
        payload.past_due_count = figures.past_due_count
    return payload


async def _load(session: AsyncSession, schedule_id: uuid.UUID, workspace_id: uuid.UUID):
    schedule = await svc.get_schedule(session, schedule_id, workspace_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


async def _load_invoice(session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID) -> Invoice:
    invoice = await invoice_service.get_invoice(session, invoice_id, workspace_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


async def _read(session: AsyncSession, schedule_id: uuid.UUID, workspace_id: uuid.UUID) -> ScheduleRead:
    schedule = await _load(session, schedule_id, workspace_id)
    figures = await svc.figures_for(session, workspace_id, [schedule.id])
    return _serialize(schedule, figures.get(schedule.id))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@router.get("/summary", response_model=ScheduleSummaryRead)
async def summary(
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return await svc.summary(session, ctx.workspace.id)


@router.get("", response_model=list[ScheduleRead])
async def list_schedules(
    status_: Optional[str] = Query(None, alias="status"),
    payee_id: Optional[uuid.UUID] = Query(None),
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedules = await svc.list_schedules(session, ctx.workspace.id, status=status_, payee_id=payee_id)
    figures = await svc.figures_for(session, ctx.workspace.id, [s.id for s in schedules])
    return [_serialize(s, figures.get(s.id)) for s in schedules]


@router.get("/{schedule_id}", response_model=ScheduleRead)
async def get_schedule(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return await _read(session, schedule_id, ctx.workspace.id)


@router.get("/{schedule_id}/invoices", response_model=list[InvoiceRead])
async def list_schedule_invoices(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    invoices = await invoice_service.list_invoices(
        session, ctx.workspace.id, schedule_id=schedule.id, limit=500
    )
    return [_serialize_invoice(inv) for inv in invoices]


@router.get("/{schedule_id}/periods", response_model=list[SchedulePeriodRead])
async def list_periods(
    schedule_id: uuid.UUID,
    ahead: int = Query(3, ge=0, le=24, description="Periods past the cursor to include"),
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """The agreement's periods as labels, from the first to a few past
    the cursor, each saying whether an invoice already answers for it.
    What the "which period is this invoice for" picker lists."""
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    taken = {
        row[0]
        for row in (
            await session.execute(
                select(Invoice.sequence).where(Invoice.schedule_id == schedule.id)
            )
        ).all()
    }
    last = schedule.next_sequence + ahead
    out = []
    for sequence in range(1, last + 1):
        if not svc.period_within_end(schedule, sequence):
            break
        start, end = svc.period_bounds(schedule, sequence)
        out.append(
            SchedulePeriodRead(
                sequence=sequence, period_start=start, period_end=end, taken=sequence in taken
            )
        )
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = payload.model_dump(exclude_unset=True)
    data["lines"] = [dict(line) for line in data["lines"]]
    try:
        schedule = await svc.create_schedule(session, ctx.workspace.id, ctx.user_id, data)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule.id, ctx.workspace.id)


@router.patch("/{schedule_id}", response_model=ScheduleRead)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.update_schedule(session, schedule, payload.model_dump(exclude_unset=True))
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.delete_schedule(session, schedule)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()


@router.post("/{schedule_id}/pause", response_model=ScheduleRead)
async def pause_schedule(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.pause_schedule(session, schedule)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.post("/{schedule_id}/resume", response_model=ScheduleRead)
async def resume_schedule(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.resume_schedule(session, schedule)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.post("/{schedule_id}/end", response_model=ScheduleRead)
async def end_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleEnd,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.end_schedule(session, schedule, reason=payload.reason, ended_at=payload.ended_at)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.post("/{schedule_id}/generate", response_model=list[InvoiceRead], status_code=status.HTTP_201_CREATED)
async def generate_now(
    schedule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """Emit the next period now, whether or not its date has come."""
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    if schedule.status != "active":
        raise HTTPException(
            status_code=400,
            detail={"code": "schedule_not_active", "message": "Only an active agreement emits invoices"},
        )
    try:
        emitted = await svc.generate_due(session, schedule, force_next=True)
    except InvoiceError as exc:
        # The failure count is worth keeping. A 409 already rolled back:
        # the period exists, and there is nothing to record.
        if exc.status_code != 409:
            await session.commit()
        raise _http(exc)
    await session.commit()
    out = []
    for emitted_invoice in emitted:
        loaded = await invoice_service.get_invoice(session, emitted_invoice.id, ctx.workspace.id)
        if loaded is None:
            continue
        await svc.after_generation(session, loaded, ctx.workspace)
        out.append(_serialize_invoice(await _load_invoice(session, loaded.id, ctx.workspace.id)))
    return out


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------
@router.post("/{schedule_id}/terms", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def add_term(
    schedule_id: uuid.UUID,
    payload: ScheduleTermInput,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    try:
        await svc.add_term(
            session,
            schedule,
            effective_from=payload.effective_from,
            lines=[dict(line) for line in payload.lines],
            discount=payload.discount,
        )
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.patch("/{schedule_id}/terms/{term_id}", response_model=ScheduleRead)
async def update_term(
    schedule_id: uuid.UUID,
    term_id: uuid.UUID,
    payload: ScheduleTermUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    term = next((t for t in schedule.terms if t.id == term_id), None)
    if term is None:
        raise HTTPException(status_code=404, detail="Term not found")
    data = payload.model_dump(exclude_unset=True)
    try:
        await svc.update_term(
            session,
            schedule,
            term,
            lines=[dict(line) for line in data["lines"]] if data.get("lines") is not None else None,
            discount=data.get("discount"),
            effective_from=data.get("effective_from"),
        )
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


@router.delete("/{schedule_id}/terms/{term_id}", response_model=ScheduleRead)
async def delete_term(
    schedule_id: uuid.UUID,
    term_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    term = next((t for t in schedule.terms if t.id == term_id), None)
    if term is None:
        raise HTTPException(status_code=404, detail="Term not found")
    try:
        await svc.delete_term(session, schedule, term)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, schedule_id, ctx.workspace.id)


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------
@router.post("/{schedule_id}/link", response_model=InvoiceRead)
async def link_invoice(
    schedule_id: uuid.UUID,
    payload: ScheduleLinkInvoice,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    schedule = await _load(session, schedule_id, ctx.workspace.id)
    invoice = await invoice_service.get_invoice(session, payload.invoice_id, ctx.workspace.id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        await svc.link_invoice(session, schedule, invoice, payload.period_start)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize_invoice(await _load_invoice(session, invoice.id, ctx.workspace.id))
