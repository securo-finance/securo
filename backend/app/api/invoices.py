import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.invoice import (
    InvoiceCreate,
    InvoicePaymentCreate,
    InvoicePaymentRead,
    InvoiceRead,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.services import invoice_service

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


class PaginatedInvoices(BaseModel):
    items: list[InvoiceSummary]
    total: int
    page: int
    limit: int


@router.get("", response_model=PaginatedInvoices)
async def list_invoices(
    payee_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    invoices, total = await invoice_service.list_invoices(
        session,
        workspace_id=ctx.workspace.id,
        payee_id=payee_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        page=page,
        limit=limit,
    )
    return PaginatedInvoices(
        items=[InvoiceSummary.model_validate(inv) for inv in invoices],
        total=total,
        page=page,
        limit=limit,
    )


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    invoice_in: InvoiceCreate,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.create_invoice(
        session, ctx.workspace.id, ctx.user_id, invoice_in
    )
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.patch("/{invoice_id}", response_model=InvoiceRead)
async def update_invoice(
    invoice_id: uuid.UUID,
    invoice_in: InvoiceUpdate,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return await invoice_service.update_invoice(session, invoice, invoice_in)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        await invoice_service.delete_invoice(session, invoice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{invoice_id}/payments", response_model=InvoiceRead)
async def record_payment(
    invoice_id: uuid.UUID,
    payment_in: InvoicePaymentCreate,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return await invoice_service.record_payment(
        session,
        invoice,
        payment_in.amount,
        payment_in.date,
        payment_in.notes,
        payment_in.transaction_id,
    )


@router.post("/{invoice_id}/send", response_model=InvoiceRead)
async def send_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        return await invoice_service.send_invoice(session, invoice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{invoice_id}/cancel", response_model=InvoiceRead)
async def cancel_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    invoice = await invoice_service.get_invoice(session, invoice_id, ctx.workspace.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        return await invoice_service.cancel_invoice(session, invoice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
