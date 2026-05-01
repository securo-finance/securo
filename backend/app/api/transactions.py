import csv
import io
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.transaction import BulkCategorizeRequest, BulkTagsRequest, LinkTransferRequest, TransactionCreate, TransactionRead, TransactionUpdate, TransferCreate, TransferRead
from app.services import transaction_service
from app.services.admin_service import get_credit_card_accounting_mode

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _tag_fx_fallback(tx: TransactionRead, primary_currency: str) -> TransactionRead:
    """Set fx_fallback=True when a cross-currency tx used 1:1 fallback rate."""
    if tx.currency != primary_currency and tx.fx_rate_used is not None and tx.fx_rate_used == 1.0:
        tx.fx_fallback = True
    return tx


class PaginatedTransactions(BaseModel):
    items: list[TransactionRead]
    total: int
    page: int
    limit: int


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
    unbilled_only: bool = Query(False, description="Cycle-math fallback only: exclude txs already linked to any bill (used for in-progress CC cycles)"),
    q: Optional[str] = Query(None),
    uncategorized: bool = Query(False),
    type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    include_opening_balance: bool = Query(False),
    exclude_transfers: bool = Query(False),
    tags: Optional[List[str]] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    accounting_mode = await get_credit_card_accounting_mode(session)
    transactions, total = await transaction_service.get_transactions(
        session, user.id,
        account_ids=_merge_id_filters(account_id, account_ids),
        category_ids=_merge_id_filters(category_id, category_ids),
        payee_id=payee_id, from_date=from_date, to_date=to_date, page=page, limit=limit,
        include_opening_balance=include_opening_balance, search=q, uncategorized=uncategorized,
        txn_type=type, exclude_transfers=exclude_transfers,
        accounting_mode=accounting_mode,
        tags=tags,
        bill_id=bill_id,
        unbilled_only=unbilled_only,
    )
    primary_currency = user.primary_currency
    items = [_tag_fx_fallback(TransactionRead.model_validate(tx, from_attributes=True), primary_currency) for tx in transactions]
    return PaginatedTransactions(items=items, total=total, page=page, limit=limit)


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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    accounting_mode = await get_credit_card_accounting_mode(session)
    transactions, _ = await transaction_service.get_transactions(
        session, user.id,
        account_ids=_merge_id_filters(account_id, account_ids),
        category_ids=_merge_id_filters(category_id, category_ids),
        payee_id=payee_id, from_date=from_date, to_date=to_date,
        search=q, uncategorized=uncategorized, txn_type=type, skip_pagination=True,
        accounting_mode=accounting_mode,
        tags=tags,
    )

    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM for Excel
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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    count = await transaction_service.bulk_update_category(
        session, user.id, data.transaction_ids, data.category_id
    )
    return {"updated": count}


@router.patch("/bulk-add-tags")
async def bulk_add_tags(
    data: BulkTagsRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    count = await transaction_service.bulk_add_tags(
        session, user.id, data.transaction_ids, data.tags
    )
    return {"updated": count}


@router.patch("/bulk-remove-tags")
async def bulk_remove_tags(
    data: BulkTagsRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    count = await transaction_service.bulk_remove_tags(
        session, user.id, data.transaction_ids, data.tags
    )
    return {"updated": count}


@router.post("/transfer", response_model=TransferRead, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    data: TransferCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        debit_tx, credit_tx = await transaction_service.create_transfer(session, user.id, data)
        debit_full = await transaction_service.get_transaction(session, debit_tx.id, user.id)
        credit_full = await transaction_service.get_transaction(session, credit_tx.id, user.id)
        primary_currency = user.primary_currency
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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Link two existing transactions as an inter-account transfer pair."""
    try:
        debit_tx, credit_tx = await transaction_service.link_existing_as_transfer(
            session, user.id, data.transaction_ids
        )
        debit_full = await transaction_service.get_transaction(session, debit_tx.id, user.id)
        credit_full = await transaction_service.get_transaction(session, credit_tx.id, user.id)
        primary_currency = user.primary_currency
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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Return ranked candidate transactions to link as a transfer counterpart."""
    anchor = await transaction_service.get_transaction(session, transaction_id, user.id)
    if not anchor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    candidates = await transaction_service.get_transfer_candidates(
        session, user.id, transaction_id, limit=limit, window_days=window_days
    )
    primary_currency = user.primary_currency
    return [
        _tag_fx_fallback(TransactionRead.model_validate(tx, from_attributes=True), primary_currency)
        for tx in candidates
    ]


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(
    transaction_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    transaction = await transaction_service.get_transaction(session, transaction_id, user.id)
    if not transaction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    primary_currency = user.primary_currency
    return _tag_fx_fallback(TransactionRead.model_validate(transaction, from_attributes=True), primary_currency)


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    data: TransactionCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        transaction = await transaction_service.create_transaction(session, user.id, data)
        full_tx = await transaction_service.get_transaction(session, transaction.id, user.id)
        primary_currency = user.primary_currency
        return _tag_fx_fallback(TransactionRead.model_validate(full_tx, from_attributes=True), primary_currency)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{transaction_id}", response_model=TransactionRead)
async def update_transaction(
    transaction_id: uuid.UUID,
    data: TransactionUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        transaction = await transaction_service.update_transaction(session, transaction_id, user.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not transaction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    primary_currency = user.primary_currency
    return _tag_fx_fallback(TransactionRead.model_validate(transaction, from_attributes=True), primary_currency)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    deleted = await transaction_service.delete_transaction(session, transaction_id, user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
