import csv
import io
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.transaction import BulkAddToGroupRequest, BulkCategorizeRequest, BulkTagsRequest, CreateCounterpartRequest, LinkTransferRequest, TransactionCreate, TransactionRead, TransactionUpdate, TransferCreate, TransferRead
from app.services import transaction_service
from app.services.admin_service import get_credit_card_accounting_mode

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _tag_fx_fallback(tx: TransactionRead, primary_currency: str) -> TransactionRead:
    """Set fx_fallback=True when a cross-currency tx used 1:1 fallback rate."""
    if tx.currency != primary_currency and tx.fx_rate_used is not None and tx.fx_rate_used == 1.0:
        tx.fx_fallback = True
    return tx


class TransactionsSummary(BaseModel):
    """Income / expense / net totals across all rows matching the active
    filters (issue #185). Amounts are in the user's primary currency.
    Floats (not Decimal) so the JSON payload matches `amount_primary`
    and the frontend gets plain numbers.

    `excluded` (issue #242) is the absolute total of everything filtered
    out of income/expense for the same rows — paired transfers,
    `treat_as_transfer` categories (transfers, investments, custom) and
    ignored items — i.e. the complement of `counts_as_pnl()`."""
    income: float
    expense: float
    net: float
    excluded: float
    currency: str


class PaginatedTransactions(BaseModel):
    items: list[TransactionRead]
    total: int
    page: int
    limit: int
    summary: Optional[TransactionsSummary] = None


def _merge_id_filters(
    single: Optional[uuid.UUID], many: Optional[List[uuid.UUID]]
) -> Optional[List[uuid.UUID]]:
    """Combine the legacy single-id query param with the new list param."""
    ids: list[uuid.UUID] = []
    if many:
        ids.extend(many)
    if single and single not in ids:
        ids.append(single)
    return ids or None


@router.get("", response_model=PaginatedTransactions)
async def list_transactions(
    account_id: Optional[uuid.UUID] = Query(None),
    account_ids: Optional[List[uuid.UUID]] = Query(None),
    category_id: Optional[uuid.UUID] = Query(None),
    category_ids: Optional[List[uuid.UUID]] = Query(None),
    payee_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    bill_id: Optional[uuid.UUID] = Query(None, description="Filter by credit-card bill (issue #92); takes precedence over from/to"),
    group_id: Optional[uuid.UUID] = Query(None, description="Filter to transactions split through this group; widens visibility for linked members"),
    unbilled_only: bool = Query(False, description="Cycle-math fallback only: exclude txs already linked to any bill (used for in-progress CC cycles)"),
    q: Optional[str] = Query(None),
    uncategorized: bool = Query(False),
    type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    include_opening_balance: bool = Query(False),
    exclude_transfers: bool = Query(False),
    tags: Optional[List[str]] = Query(None),
    min_amount: Optional[float] = Query(None, ge=0, description="Filter to transactions with absolute amount >= this value (primary currency)."),
    max_amount: Optional[float] = Query(None, ge=0, description="Filter to transactions with absolute amount <= this value (primary currency)."),
    sort_by: Optional[str] = Query(None, description="Column to sort by (date|amount|description|payee|category|account|type|status). Default: date desc."),
    sort_dir: str = Query("desc", regex="^(asc|desc)$"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    accounting_mode = await get_credit_card_accounting_mode(session)
    transactions, total, summary = await transaction_service.get_transactions(
        session, ctx.workspace.id, ctx.user_id,
        account_ids=_merge_id_filters(account_id, account_ids),
        category_ids=_merge_id_filters(category_id, category_ids),
        payee_id=payee_id, from_date=from_date, to_date=to_date, page=page, limit=limit,
        include_opening_balance=include_opening_balance, search=q, uncategorized=uncategorized,
        txn_type=type, exclude_transfers=exclude_transfers,
        accounting_mode=accounting_mode,
        tags=tags,
        bill_id=bill_id,
        group_id=group_id,
        unbilled_only=unbilled_only,
        sort_by=sort_by,
        sort_dir=sort_dir,
        min_amount=min_amount,
        max_amount=max_amount,
        include_summary=True,
    )
    primary_currency = ctx.user.primary_currency
    items = [_tag_fx_fallback(TransactionRead.model_validate(tx, from_attributes=True), primary_currency) for tx in transactions]
    summary_out = (
        TransactionsSummary(**summary, currency=primary_currency)
        if summary is not None
        else None
    )
    return PaginatedTransactions(items=items, total=total, page=page, limit=limit, summary=summary_out)


@router.get("/export")
async def export_transactions(
    account_id: Optional[uuid.UUID] = Query(None),
    account_ids: Optional[List[uuid.UUID]] = Query(None),
    category_id: Optional[uuid.UUID] = Query(None),
    category_ids: Optional[List[uuid.UUID]] = Query(None),
    payee_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    q: Optional[str] = Query(None),
    uncategorized: bool = Query(False),
    type: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    transaction_ids: Optional[List[uuid.UUID]] = Query(None, description="If set, exports exactly these rows (scoped to the workspace); other filters are ignored."),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    accounting_mode = await get_credit_card_accounting_mode(session)
    if transaction_ids:
        # Selection-only export: bypass user-facing filters but keep the
        # service-level workspace/visibility scoping intact.
        transactions, _, _ = await transaction_service.get_transactions(
            session, ctx.workspace.id, ctx.user_id,
            skip_pagination=True,
            accounting_mode=accounting_mode,
            transaction_ids=transaction_ids,
        )
    else:
        transactions, _, _ = await transaction_service.get_transactions(
            session, ctx.workspace.id, ctx.user_id,
            account_ids=_merge_id_filters(account_id, account_ids),
            category_ids=_merge_id_filters(category_id, category_ids),
            payee_id=payee_id, from_date=from_date, to_date=to_date,
            search=q, uncategorized=uncategorized, txn_type=type, skip_pagination=True,
            accounting_mode=accounting_mode,
            tags=tags,
        )

    output = io.StringIO()
    output.write("﻿")  # UTF-8 BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["date", "description", "amount", "type", "currency", "category", "account", "payee", "payee_name", "notes", "status", "source", "amount_primary", "fx_rate_used"])
    for tx in transactions:
        writer.writerow([
            tx.date.isoformat(),
            tx.description,
            str(tx.amount),
            tx.type,
            tx.currency,
            tx.category.name if tx.category else "",
            tx.account.name if tx.account else "",
            tx.payee or "",
            getattr(tx, "payee_name", "") or "",
            tx.notes or "",
            tx.status,
            tx.source,
            str(tx.amount_primary) if tx.amount_primary is not None else "",
            str(tx.fx_rate_used) if tx.fx_rate_used is not None else "",
        ])

    output.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="transactions-{today}.csv"'},
    )


@router.patch("/bulk-categorize")
async def bulk_categorize(
    data: BulkCategorizeRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await transaction_service.bulk_update_category(
        session, ctx.workspace.id, data.transaction_ids, data.category_id
    )
    return {"updated": count}


@router.patch("/bulk-add-tags")
async def bulk_add_tags(
    data: BulkTagsRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await transaction_service.bulk_add_tags(
        session, ctx.workspace.id, data.transaction_ids, data.tags
    )
    return {"updated": count}


@router.patch("/bulk-remove-tags")
async def bulk_remove_tags(
    data: BulkTagsRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await transaction_service.bulk_remove_tags(
        session, ctx.workspace.id, data.transaction_ids, data.tags
    )
    return {"updated": count}


@router.patch("/bulk-add-to-group")
async def bulk_add_to_group(
    data: BulkAddToGroupRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await transaction_service.bulk_add_to_group(
            session,
            ctx.workspace.id,
            ctx.user_id,
            data.transaction_ids,
            data.group_id,
            share_type=data.share_type,
            member_splits=data.member_splits,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/transfer", response_model=TransferRead, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    data: TransferCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        debit_tx, credit_tx = await transaction_service.create_transfer(
            session, ctx.workspace.id, ctx.user_id, data
        )
        debit_full = await transaction_service.get_transaction(session, debit_tx.id, ctx.workspace.id)
        credit_full = await transaction_service.get_transaction(session, credit_tx.id, ctx.workspace.id)
        primary_currency = ctx.user.primary_currency
        return TransferRead(
            debit=_tag_fx_fallback(TransactionRead.model_validate(debit_full, from_attributes=True), primary_currency),
            credit=_tag_fx_fallback(TransactionRead.model_validate(credit_full, from_attributes=True), primary_currency),
            transfer_pair_id=debit_tx.transfer_pair_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/link-transfer", response_model=TransferRead)
async def link_transfer(
    data: LinkTransferRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Link two existing transactions as an inter-account transfer pair."""
    try:
        debit_tx, credit_tx = await transaction_service.link_existing_as_transfer(
            session, ctx.workspace.id, data.transaction_ids
        )
        debit_full = await transaction_service.get_transaction(session, debit_tx.id, ctx.workspace.id)
        credit_full = await transaction_service.get_transaction(session, credit_tx.id, ctx.workspace.id)
        primary_currency = ctx.user.primary_currency
        return TransferRead(
            debit=_tag_fx_fallback(TransactionRead.model_validate(debit_full, from_attributes=True), primary_currency),
            credit=_tag_fx_fallback(TransactionRead.model_validate(credit_full, from_attributes=True), primary_currency),
            transfer_pair_id=debit_tx.transfer_pair_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{transaction_id}/create-counterpart", response_model=TransferRead, status_code=status.HTTP_201_CREATED)
async def create_counterpart(
    transaction_id: uuid.UUID,
    data: CreateCounterpartRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Mark a transaction as a transfer by auto-creating its counterpart in
    another (typically manual) account."""
    try:
        debit_tx, credit_tx = await transaction_service.create_transfer_counterpart(
            session, ctx.workspace.id, ctx.user_id, transaction_id, data.to_account_id
        )
        debit_full = await transaction_service.get_transaction(session, debit_tx.id, ctx.workspace.id)
        credit_full = await transaction_service.get_transaction(session, credit_tx.id, ctx.workspace.id)
        primary_currency = ctx.user.primary_currency
        return TransferRead(
            debit=_tag_fx_fallback(TransactionRead.model_validate(debit_full, from_attributes=True), primary_currency),
            credit=_tag_fx_fallback(TransactionRead.model_validate(credit_full, from_attributes=True), primary_currency),
            transfer_pair_id=debit_tx.transfer_pair_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{transaction_id}/transfer-candidates", response_model=list[TransactionRead])
async def get_transfer_candidates(
    transaction_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=50),
    window_days: int = Query(30, ge=1, le=365),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Return ranked candidate transactions to link as a transfer counterpart."""
    anchor = await transaction_service.get_transaction(session, transaction_id, ctx.workspace.id)
    if not anchor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    candidates = await transaction_service.get_transfer_candidates(
        session, ctx.workspace.id, transaction_id, limit=limit, window_days=window_days
    )
    primary_currency = ctx.user.primary_currency
    return [
        _tag_fx_fallback(TransactionRead.model_validate(tx, from_attributes=True), primary_currency)
        for tx in candidates
    ]


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(
    transaction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    transaction = await transaction_service.get_transaction(session, transaction_id, ctx.workspace.id)
    if not transaction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    primary_currency = ctx.user.primary_currency
    return _tag_fx_fallback(TransactionRead.model_validate(transaction, from_attributes=True), primary_currency)


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    data: TransactionCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        transaction = await transaction_service.create_transaction(
            session, ctx.workspace.id, ctx.user_id, data
        )
        full_tx = await transaction_service.get_transaction(session, transaction.id, ctx.workspace.id)
        primary_currency = ctx.user.primary_currency
        return _tag_fx_fallback(TransactionRead.model_validate(full_tx, from_attributes=True), primary_currency)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{transaction_id}", response_model=TransactionRead)
async def update_transaction(
    transaction_id: uuid.UUID,
    data: TransactionUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        transaction = await transaction_service.update_transaction(
            session, transaction_id, ctx.workspace.id, ctx.user_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not transaction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    primary_currency = ctx.user.primary_currency
    return _tag_fx_fallback(TransactionRead.model_validate(transaction, from_attributes=True), primary_currency)


@router.patch("/{transaction_id}/ignore", response_model=TransactionRead)
async def toggle_ignore_transaction(
    transaction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    transaction = await transaction_service.toggle_ignore_transaction(
        session, transaction_id, ctx.workspace.id
    )
    if not transaction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    primary_currency = ctx.user.primary_currency
    return _tag_fx_fallback(TransactionRead.model_validate(transaction, from_attributes=True), primary_currency)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await transaction_service.delete_transaction(
        session, transaction_id, ctx.workspace.id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
