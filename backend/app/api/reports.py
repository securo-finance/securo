import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.report import ReportResponse
from app.services import report_service

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Cap the user-picked custom range so wide windows can't tie up the DB with
# per-day snapshot fan-out. Ten years covers every realistic personal-finance
# question and stays inside the existing daily-interval budget.
_MAX_CUSTOM_RANGE_DAYS = 3660


def _financial_year_start_month(tax_jurisdiction: str | None) -> int:
    """Return the first month of the workspace's financial year."""
    return 4 if (tax_jurisdiction or "").upper() == "IN" else 1


def _reject_unsupported_fiscal_year_report(
    period: str | None, interval: str, financial_year_start_month: int
) -> None:
    if period == "ytd" and interval == "yearly" and financial_year_start_month != 1:
        raise HTTPException(
            status_code=422,
            detail="Yearly YTD reports are not supported for non-calendar financial years",
        )


def _resolve_custom_range(
    start_date: date | None, end_date: date | None
) -> tuple[date | None, date | None]:
    """Validate a user-supplied custom range and return the (start, end) pair.

    Both endpoints must be provided together; the range must be non-empty and
    stay within :data:`_MAX_CUSTOM_RANGE_DAYS`. Returns ``(None, None)`` when
    neither is set, so callers can fall back to the preset windows.
    """
    if start_date is None and end_date is None:
        return None, None
    if start_date is None or end_date is None:
        raise HTTPException(
            status_code=422,
            detail="start_date and end_date must be provided together",
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="end_date must be on or after start_date",
        )
    span_days = (end_date - start_date).days + 1
    if span_days > _MAX_CUSTOM_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Custom range is too wide (max {_MAX_CUSTOM_RANGE_DAYS} days)"
            ),
        )
    return start_date, end_date


@router.get("/net-worth", response_model=ReportResponse)
async def get_net_worth(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    asset_group_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    financial_year_start_month = _financial_year_start_month(ctx.workspace.tax_jurisdiction)
    _reject_unsupported_fiscal_year_report(period, interval, financial_year_start_month)
    custom_start, custom_end = _resolve_custom_range(start_date, end_date)
    return await report_service.get_net_worth_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, asset_group_ids=asset_group_ids, period=period,
        financial_year_start_month=financial_year_start_month,
        start_date=custom_start, end_date=custom_end,
    )


@router.get("/income-expenses", response_model=ReportResponse)
async def get_income_expenses(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    days: Optional[int] = Query(None, ge=1, le=730),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """`days` overrides `months` with an exact rolling window ending today.

    Alternatively `start_date`/`end_date` (both required together) pin the
    window to an explicit calendar range, overriding `months`, `period`, and
    `days`.
    """
    financial_year_start_month = _financial_year_start_month(ctx.workspace.tax_jurisdiction)
    _reject_unsupported_fiscal_year_report(period, interval, financial_year_start_month)
    custom_start, custom_end = _resolve_custom_range(start_date, end_date)
    return await report_service.get_income_expenses_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, period=period, days=days,
        financial_year_start_month=financial_year_start_month,
        start_date=custom_start, end_date=custom_end,
    )


@router.get("/cash-flow", response_model=ReportResponse)
async def get_cash_flow(
    months: int = Query(6, ge=1, le=12),
    interval: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    baseline: bool = Query(False),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_service.get_cash_flow_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        baseline=baseline, account_ids=account_ids,
    )
