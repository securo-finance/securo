import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.dashboard_service import get_occurrences_in_range
from app.services.transaction_service import _materialized_recurring_occurrences


class ReconciliationMatch(BaseModel):
    match_type: str  # "recurring" or "invoice"
    confidence: str  # "high", "medium", "low"
    expected_id: str # recurring_id or invoice_id
    expected_amount: float
    expected_date: date
    expected_description: str
    expected_payee_id: Optional[uuid.UUID] = None


class ReconciliationSuggestion(BaseModel):
    transaction: dict
    matches: List[ReconciliationMatch]


async def get_reconciliation_suggestions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    account_ids: Optional[List[uuid.UUID]] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> List[ReconciliationSuggestion]:
    """Finds newly synced transactions and suggests expected transactions they might match."""
    # Find active recurring transactions
    recurring_stmt = select(RecurringTransaction).where(
        RecurringTransaction.workspace_id == workspace_id,
        RecurringTransaction.is_active == True,
    )
    recurring_result = await session.execute(recurring_stmt)
    recurrings = list(recurring_result.scalars().all())

    # Find open invoices
    invoice_stmt = select(Invoice).where(
        Invoice.workspace_id == workspace_id,
        Invoice.status.in_(["sent", "partial", "overdue"]),
    ).options(selectinload(Invoice.payee))
    invoice_result = await session.execute(invoice_stmt)
    invoices = list(invoice_result.scalars().all())

    # Find recent synced transactions (not linked to recurring or invoice, and non-transfers)
    filters = [
        Transaction.workspace_id == workspace_id,
        Transaction.source != "manual",
        Transaction.recurring_transaction_id.is_(None),
        Transaction.invoice_id.is_(None),
        Transaction.is_ignored == False,
    ]
    if account_ids:
        filters.append(Transaction.account_id.in_(account_ids))
    if from_date:
        filters.append(Transaction.date >= from_date)
    if to_date:
        filters.append(Transaction.date <= to_date)
        
    tx_stmt = select(Transaction).where(*filters).order_by(Transaction.date.desc()).limit(100)
    tx_result = await session.execute(tx_stmt)
    transactions = list(tx_result.scalars().all())

    if not transactions:
        return []

    # Get materialized occurrences for deduplication of recurring
    min_date = min(t.date for t in transactions)
    max_date = max(t.date for t in transactions)
    materialized = await _materialized_recurring_occurrences(session, workspace_id, min_date, max_date)

    suggestions = []
    
    for tx in transactions:
        tx_dict = {
            "id": str(tx.id),
            "date": tx.date.isoformat(),
            "amount": float(tx.amount),
            "description": tx.description,
            "payee_id": str(tx.payee_id) if tx.payee_id else None,
            "type": tx.type,
        }
        matches = []
        
        # 1. Match against Invoices
        for inv in invoices:
            # Basic sanity checks: sign matching
            if tx.type == "credit" and inv.total > 0:
                pass # Income matches positive invoice
            elif tx.type == "debit" and inv.total < 0:
                pass
            else:
                continue

            amount_diff = abs(abs(float(tx.amount)) - float(inv.amount_due))
            
            # High confidence if amount is exact and payee matches
            if amount_diff < 0.05 and tx.payee_id and tx.payee_id == inv.payee_id:
                matches.append(ReconciliationMatch(
                    match_type="invoice",
                    confidence="high",
                    expected_id=str(inv.id),
                    expected_amount=float(inv.amount_due),
                    expected_date=inv.due_date,
                    expected_description=f"Invoice {inv.invoice_number}",
                    expected_payee_id=inv.payee_id,
                ))
            elif amount_diff < 0.05:
                matches.append(ReconciliationMatch(
                    match_type="invoice",
                    confidence="medium",
                    expected_id=str(inv.id),
                    expected_amount=float(inv.amount_due),
                    expected_date=inv.due_date,
                    expected_description=f"Invoice {inv.invoice_number}",
                    expected_payee_id=inv.payee_id,
                ))
                
        # 2. Match against Recurring Transactions
        for rt in recurrings:
            # Skip if types don't match
            if tx.type != rt.type:
                continue
                
            # Get expected dates around the transaction date
            occurrences = get_occurrences_in_range(rt, tx.date, tx.date)
            # Expand search to +/- 7 days for matching
            if not occurrences:
                from datetime import timedelta
                occurrences = get_occurrences_in_range(rt, tx.date - timedelta(days=7), tx.date + timedelta(days=7))
                
            for occ_date in occurrences:
                # Check if this occurrence is already materialized
                if rt.id in materialized and occ_date in materialized[rt.id]:
                    continue
                    
                amount_diff = abs(abs(float(tx.amount)) - abs(float(rt.amount)))
                date_diff = abs((tx.date - occ_date).days)
                
                if amount_diff < 0.05 and date_diff <= 3:
                    confidence = "high" if tx.payee_id == rt.payee_id else "medium"
                    matches.append(ReconciliationMatch(
                        match_type="recurring",
                        confidence=confidence,
                        expected_id=str(rt.id),
                        expected_amount=float(rt.amount),
                        expected_date=occ_date,
                        expected_description=rt.description,
                        expected_payee_id=rt.payee_id,
                    ))
                elif amount_diff < float(rt.amount) * 0.1 and date_diff <= 7:
                    # Within 10% amount difference and 7 days
                    matches.append(ReconciliationMatch(
                        match_type="recurring",
                        confidence="low",
                        expected_id=str(rt.id),
                        expected_amount=float(rt.amount),
                        expected_date=occ_date,
                        expected_description=rt.description,
                        expected_payee_id=rt.payee_id,
                    ))

        if matches:
            # Sort matches by confidence
            matches.sort(key=lambda m: {"high": 0, "medium": 1, "low": 2}[m.confidence])
            suggestions.append(ReconciliationSuggestion(transaction=tx_dict, matches=matches))
            
    return suggestions


async def apply_reconciliation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transaction_id: uuid.UUID,
    match_type: str,
    expected_id: uuid.UUID,
) -> Transaction:
    """Links a transaction to an expected occurrence or invoice."""
    stmt = select(Transaction).where(
        Transaction.id == transaction_id, 
        Transaction.workspace_id == workspace_id
    )
    result = await session.execute(stmt)
    tx = result.scalar_one_or_none()
    
    if not tx:
        raise ValueError("Transaction not found")
        
    if match_type == "recurring":
        tx.recurring_transaction_id = expected_id
    elif match_type == "invoice":
        tx.invoice_id = expected_id
        # Also record the payment on the invoice
        from app.services.invoice_service import get_invoice, record_payment
        invoice = await get_invoice(session, expected_id, workspace_id)
        if invoice:
            await record_payment(session, invoice, abs(tx.amount), tx.date, "Reconciled automatically", tx.id)
    else:
        raise ValueError("Invalid match type")
        
    await session.commit()
    return tx
