import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.account import (
    AccountCreate,
    AccountRead,
    AccountSummary,
    AccountUpdate,
    CreditCardBillRead,
)
from app.services import account_service
from app.services.fx_rate_service import convert

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountRead])
async def list_accounts(
    include_closed: bool = False,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    accounts = await account_service.get_accounts(session, user.id, include_closed=include_closed)
    primary_currency = user.primary_currency
    for acc in accounts:
        if acc["currency"] != primary_currency:
            converted, _ = await convert(
                session, Decimal(str(acc["current_balance"])), acc["currency"], primary_currency,
            )
            acc["balance_primary"] = float(converted)
    return accounts


@router.get("/{account_id}/summary", response_model=AccountSummary)
async def get_account_summary(
    account_id: uuid.UUID,
    date_from: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD"),
    bill_id: Optional[uuid.UUID] = Query(None, description="Aggregate by bill_id (issue #92); takes precedence over from/to"),
    unbilled_only: bool = Query(False, description="Cycle-math fallback only: exclude txs already linked to any bill"),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    from_date = date.fromisoformat(date_from) if date_from else None
    to_date = date.fromisoformat(date_to) if date_to else None
    summary = await account_service.get_account_summary(
        session, account_id, user.id, date_from=from_date, date_to=to_date,
        bill_id=bill_id, unbilled_only=unbilled_only,
    )
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    account = await account_service.get_account(session, account_id, user.id)
    primary_currency = user.primary_currency
    if account and account.currency != primary_currency:
        bal, _ = await convert(session, Decimal(str(summary["current_balance"])), account.currency, primary_currency)
        inc, _ = await convert(session, Decimal(str(summary["monthly_income"])), account.currency, primary_currency)
        exp, _ = await convert(session, Decimal(str(summary["monthly_expenses"])), account.currency, primary_currency)
        summary["current_balance_primary"] = float(bal)
        summary["monthly_income_primary"] = float(inc)
        summary["monthly_expenses_primary"] = float(exp)

    return summary


@router.get("/{account_id}/balance-history")
async def get_account_balance_history(
    account_id: uuid.UUID,
    date_from: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD"),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    from_date = date.fromisoformat(date_from) if date_from else None
    to_date = date.fromisoformat(date_to) if date_to else None
    history = await account_service.get_account_balance_history(
        session, account_id, user.id, date_from=from_date, date_to=to_date,
    )
    if history is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    account = await account_service.get_account(session, account_id, user.id)
    primary_currency = user.primary_currency
    if account and account.currency != primary_currency:
        for point in history:
            point_date = date.fromisoformat(point["date"])
            converted, _ = await convert(
                session, Decimal(str(point["balance"])), account.currency, primary_currency, target_date=point_date,
            )
            point["balance_primary"] = float(converted)

    return history


@router.get("/{account_id}/bills", response_model=list[CreditCardBillRead])
async def get_account_bills(
    account_id: uuid.UUID,
    limit: int = Query(24, ge=1, le=200, description="Max bills to return, newest due_date first"),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """List credit-card bills for an account, newest due_date first.

    Returns an empty list for non-CC accounts and for CC accounts that have
    no synced bills (provider doesn't expose them, or first sync hasn't
    happened). Issue #92.
    """
    bills = await account_service.get_credit_card_bills(
        session, account_id, user.id, limit=limit,
    )
    if bills is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return bills


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    account = await account_service.get_account(session, account_id, user.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account_service.serialize_account(account, None, None)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    data: AccountCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    account = await account_service.create_account(session, user.id, data)
    return account_service.serialize_account(account, None, None)


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: uuid.UUID,
    data: AccountUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        account = await account_service.update_account(session, account_id, user.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account_service.serialize_account(account, None, None)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        deleted = await account_service.delete_account(session, account_id, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")


@router.post("/{account_id}/close", response_model=AccountRead)
async def close_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        account = await account_service.close_account(session, account_id, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


@router.post("/{account_id}/reopen", response_model=AccountRead)
async def reopen_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        account = await account_service.reopen_account(session, account_id, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account
