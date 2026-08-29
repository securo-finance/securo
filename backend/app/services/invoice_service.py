import uuid
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.invoice_line_item import InvoiceLineItem
from app.models.invoice_payment import InvoicePayment
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate


async def get_invoice(
    session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Invoice]:
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.workspace_id == workspace_id)
        .options(
            selectinload(Invoice.line_items),
            selectinload(Invoice.payments),
            selectinload(Invoice.payee),
        )
    )
    result = await session.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice and invoice.payee:
        invoice.payee_name = invoice.payee.name
    return invoice


async def list_invoices(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    payee_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    page: int = 1,
    limit: int = 50,
) -> Tuple[list[Invoice], int]:
    filters = [Invoice.workspace_id == workspace_id]
    if payee_id:
        filters.append(Invoice.payee_id == payee_id)
    if status:
        filters.append(Invoice.status == status)
    if from_date:
        filters.append(Invoice.issue_date >= from_date)
    if to_date:
        filters.append(Invoice.issue_date <= to_date)

    base_query = select(Invoice).outerjoin(Payee).where(*filters)
    
    count_query = select(func.count()).select_from(base_query.subquery())
    total = await session.scalar(count_query)

    query = (
        base_query
        .options(selectinload(Invoice.payee))
        .order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    
    result = await session.execute(query)
    invoices = list(result.scalars().all())
    for invoice in invoices:
        if invoice.payee:
            invoice.payee_name = invoice.payee.name
    
    return invoices, total or 0


async def create_invoice(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    invoice_in: InvoiceCreate,
) -> Invoice:
    invoice = Invoice(
        workspace_id=workspace_id,
        user_id=user_id,
        payee_id=invoice_in.payee_id,
        invoice_number=invoice_in.invoice_number,
        status=invoice_in.status,
        currency=invoice_in.currency,
        subtotal=invoice_in.subtotal,
        total=invoice_in.total,
        amount_due=invoice_in.total,
        amount_paid=Decimal("0.00"),
        issue_date=invoice_in.issue_date,
        due_date=invoice_in.due_date,
        notes=invoice_in.notes,
        is_recurring=invoice_in.is_recurring,
        recurring_frequency=invoice_in.recurring_frequency,
        recurring_end_date=invoice_in.recurring_end_date,
        total_installments=invoice_in.total_installments,
        installment_amount=invoice_in.installment_amount,
    )
    if invoice.is_recurring and invoice.recurring_frequency:
        from app.services.recurring_transaction_service import _advance_date
        invoice.next_recurrence_date = _advance_date(invoice.issue_date, invoice.recurring_frequency)

    session.add(invoice)
    await session.flush()

    for item_in in invoice_in.line_items:
        item = InvoiceLineItem(
            invoice_id=invoice.id,
            description=item_in.description,
            quantity=item_in.quantity,
            unit_price=item_in.unit_price,
            amount=item_in.amount,
            category_id=item_in.category_id,
            sort_order=item_in.sort_order,
        )
        session.add(item)
    
    await session.commit()
    return await get_invoice(session, invoice.id, workspace_id)


async def update_invoice(
    session: AsyncSession,
    invoice: Invoice,
    invoice_in: InvoiceUpdate,
) -> Invoice:
    update_data = invoice_in.model_dump(exclude_unset=True, exclude={"line_items"})
    for field, value in update_data.items():
        setattr(invoice, field, value)
    
    # Recalculate amounts
    invoice.amount_due = invoice.total - invoice.amount_paid
    if invoice.status not in ["paid", "cancelled"]:
        if invoice.amount_due <= 0 and invoice.total > 0:
            invoice.status = "paid"
        elif invoice.amount_paid > 0:
            invoice.status = "partial"

    if invoice_in.line_items is not None:
        # Delete existing line items and recreate
        await session.execute(
            select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice.id)
        )
        for item in invoice.line_items:
            await session.delete(item)
        
        for item_in in invoice_in.line_items:
            item = InvoiceLineItem(
                invoice_id=invoice.id,
                description=item_in.description,
                quantity=item_in.quantity,
                unit_price=item_in.unit_price,
                amount=item_in.amount,
                category_id=item_in.category_id,
                sort_order=item_in.sort_order,
            )
            session.add(item)
    
    await session.commit()
    return await get_invoice(session, invoice.id, invoice.workspace_id)


async def delete_invoice(session: AsyncSession, invoice: Invoice) -> None:
    if invoice.status not in ["draft", "cancelled"]:
        raise ValueError("Only draft or cancelled invoices can be deleted")
    await session.delete(invoice)
    await session.commit()


async def record_payment(
    session: AsyncSession,
    invoice: Invoice,
    amount: Decimal,
    payment_date: date,
    notes: Optional[str] = None,
    transaction_id: Optional[uuid.UUID] = None,
) -> Invoice:
    payment = InvoicePayment(
        invoice_id=invoice.id,
        amount=amount,
        date=payment_date,
        notes=notes,
        transaction_id=transaction_id,
    )
    session.add(payment)
    
    invoice.amount_paid += amount
    invoice.amount_due = invoice.total - invoice.amount_paid
    
    if invoice.amount_due <= 0:
        invoice.status = "paid"
    elif invoice.status in ["draft", "sent", "overdue"]:
        invoice.status = "partial"
        
    if transaction_id:
        # Also link the transaction to the invoice
        result = await session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        tx = result.scalar_one_or_none()
        if tx:
            tx.invoice_id = invoice.id
            
    await session.commit()
    return await get_invoice(session, invoice.id, invoice.workspace_id)


async def send_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    if invoice.status != "draft":
        raise ValueError("Only draft invoices can be sent")
    invoice.status = "sent"
    await session.commit()
    return invoice


async def cancel_invoice(session: AsyncSession, invoice: Invoice) -> Invoice:
    if invoice.status == "paid":
        raise ValueError("Cannot cancel a paid invoice")
    invoice.status = "cancelled"
    await session.commit()
    return invoice
