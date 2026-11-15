import uuid
from datetime import date, timedelta
from typing import Literal, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction_calendar import (
    TransactionCalendarDay,
    TransactionCalendarItem,
    TransactionCalendarResponse,
)
from app.services._query_filters import reporting_date_col, reporting_date_value
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.balance_projection import (
    date_range,
    is_ignored_row,
    load_projection_base,
    positive_primary_amount,
    signed_balance_delta_primary,
    walk,
)
from app.services.dashboard_service import _get_forecast_transactions
from app.services.spending_plan_service import calendar_allowance


async def get_transaction_calendar(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
    date_basis: Literal["reporting", "bank"] = "reporting",
) -> TransactionCalendarResponse:
    """Build a read-only month-grid transaction calendar.

    The calendar shows posted/manual transactions plus virtual recurring
    occurrences. Virtual rows are never persisted; they only affect the daily
    projected ending balance returned by this read endpoint.
    """
    if month is None:
        month = app_today().replace(day=1)
    month_start = month.replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    # Sunday-start grid to match the existing mock/reference. The response
    # includes spillover days so frontend layout does not need to make extra
    # balance queries for previous/next month cells.
    grid_start = month_start - timedelta(days=(month_start.weekday() + 1) % 7)
    last_month_day = month_end - timedelta(days=1)
    grid_end = last_month_day + timedelta(days=6 - ((last_month_day.weekday() + 1) % 7) + 1)

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    accounting_mode = (
        await get_credit_card_accounting_mode(session)
        if date_basis == "reporting"
        else "cash"
    )
    activity_date_col = (
        reporting_date_col(accounting_mode)
        if date_basis == "reporting"
        else Transaction.date
    )

    requested_account_ids = account_ids
    if requested_account_ids is not None and len(requested_account_ids) == 0:
        return _empty_calendar(
            month_start, grid_start, grid_end, primary_currency, requested_account_ids
        )

    days = {
        d: TransactionCalendarDay(
            date=d,
            in_month=month_start <= d < month_end,
            ending_balance=0.0,
        )
        for d in date_range(grid_start, grid_end)
    }

    base = await load_projection_base(
        session, workspace_id, primary_currency, grid_start, grid_end, requested_account_ids
    )

    actual_rows = await _load_actual_transactions(
        session,
        workspace_id,
        grid_start,
        grid_end,
        requested_account_ids,
        activity_date_col,
    )
    for tx in actual_rows:
        activity_date = _activity_date(tx, date_basis, accounting_mode)
        if activity_date not in days:
            continue
        day = days[activity_date]
        amount_primary = await positive_primary_amount(
            session, tx.amount, tx.currency, primary_currency, tx.amount_primary
        )

        parts = _category_parts(tx)
        is_transfer = _is_transfer_item(tx, parts)
        ignored = is_ignored_row(tx)
        if not ignored and not tx.exclude_from_pnl:
            # A split row lands part by part: its transfer-like parts in the
            # transfer bucket, the rest in income or expense.
            for category, ratio in parts:
                if _is_transfer_part(tx, category):
                    transfer_delta = (
                        await signed_balance_delta_primary(session, tx, primary_currency)
                    ) * ratio
                    day.transfer_net += transfer_delta
                    day.actual_transfer_net += transfer_delta
                    day.has_transfer = True
                elif tx.source != "opening_balance":
                    part_amount = amount_primary * ratio
                    if tx.type == "credit":
                        day.income += part_amount
                        day.actual_income += part_amount
                        day.has_income = True
                    else:
                        day.expense += part_amount
                        day.actual_expense += part_amount
                        day.has_expense = True

        day.actual_count += 1
        day.items.append(
            _actual_item(tx, activity_date, amount_primary, is_transfer, ignored)
        )

    forecast_rows = await _get_forecast_transactions(
        session,
        workspace_id,
        grid_start,
        grid_end,
        requested_account_ids,
        range_date_col=activity_date_col,
        future_date_col=Transaction.date,
    )
    for tx in forecast_rows:
        activity_date = _activity_date(tx, date_basis, accounting_mode)
        if activity_date not in days:
            continue
        day = days[activity_date]
        amount_primary = await positive_primary_amount(
            session, tx.amount, tx.currency, primary_currency, tx.amount_primary
        )
        parts = _category_parts(tx)
        is_transfer = _is_transfer_item(tx, parts)
        ignored = is_ignored_row(tx)
        if not ignored and not tx.exclude_from_pnl:
            for category, ratio in parts:
                if _is_transfer_part(tx, category):
                    delta = (
                        await signed_balance_delta_primary(session, tx, primary_currency)
                    ) * ratio
                    day.transfer_net += delta
                    day.projected_transfer_net += delta
                    day.has_transfer = True
                elif tx.type == "credit":
                    day.income += amount_primary * ratio
                    day.projected_income += amount_primary * ratio
                    day.has_income = True
                else:
                    day.expense += amount_primary * ratio
                    day.projected_expense += amount_primary * ratio
                    day.has_expense = True
        day.projected_count += 1
        day.items.append(
            _forecast_item(tx, activity_date, amount_primary, is_transfer, ignored)
        )

    for item, signed_delta, parts in base.recurring_items:
        day = days[item.date]
        # Ignored projections stay out of every aggregate, exactly like ignored
        # actuals: out of the activity buckets here, and out of the balance deltas
        # built in project_recurring_items. A recurring the user chose to ignore
        # must not move a projected balance that will not move once it posts.
        if not item.is_ignored:
            # A split template lands part by part, like a split actual.
            for part_is_transfer, ratio in parts:
                part_delta = signed_delta * ratio
                part_amount = abs(part_delta)
                if part_is_transfer:
                    day.transfer_net += part_delta
                    day.projected_transfer_net += part_delta
                    day.has_transfer = True
                elif item.type == "credit":
                    day.income += part_amount
                    day.projected_income += part_amount
                    day.has_income = True
                else:
                    day.expense += part_amount
                    day.projected_expense += part_amount
                    day.has_expense = True
        day.projected_count += 1
        day.items.append(item)

    plan_allowance = await calendar_allowance(
        session, workspace_id, primary_currency, requested_account_ids
    )
    allowance, spent_today = plan_allowance or (0.0, 0.0)
    response_days: list[TransactionCalendarDay] = []
    for point in walk(base, app_today(), allowance, spent_today):
        day = days[point.date]
        day.ending_balance = point.balance
        day.projected_allowance = round(point.allowance, 2)
        day.income = round(day.income, 2)
        day.expense = round(day.expense, 2)
        day.transfer_net = round(day.transfer_net, 2)
        day.actual_income = round(day.actual_income, 2)
        day.actual_expense = round(day.actual_expense, 2)
        day.actual_transfer_net = round(day.actual_transfer_net, 2)
        day.projected_income = round(day.projected_income, 2)
        day.projected_expense = round(day.projected_expense, 2)
        day.projected_transfer_net = round(day.projected_transfer_net, 2)
        day.items.sort(key=lambda item: (item.kind != "actual", item.type, item.description.lower()))
        response_days.append(day)

    return TransactionCalendarResponse(
        month=month_start.strftime("%Y-%m"),
        currency=primary_currency,
        account_ids=requested_account_ids,
        days=response_days,
        spending_plan_applied=plan_allowance is not None,
    )


def _empty_calendar(
    month_start: date,
    grid_start: date,
    grid_end: date,
    primary_currency: str,
    account_ids: list[uuid.UUID],
) -> TransactionCalendarResponse:
    return TransactionCalendarResponse(
        month=month_start.strftime("%Y-%m"),
        currency=primary_currency,
        account_ids=account_ids,
        days=[
            TransactionCalendarDay(
                date=d,
                in_month=month_start <= d < _next_month(month_start),
                ending_balance=0.0,
            )
            for d in date_range(grid_start, grid_end)
        ],
    )


def _next_month(month_start: date) -> date:
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


async def _load_actual_transactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
    activity_date_col,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            activity_date_col >= start,
            activity_date_col < end,
            Transaction.date <= app_today(),
            Transaction.status == "posted",
        )
        .options(
            selectinload(Transaction.account),
            selectinload(Transaction.category),
            selectinload(Transaction.payee_entity),
        )
        .order_by(activity_date_col.asc(), Transaction.created_at.asc())
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _category_parts(tx: Transaction) -> list[tuple[Optional[Category], float]]:
    """`(category, share)` for each part of `tx`: one part for a plain row,
    one per category line for a row split across categories."""
    total = abs(float(tx.amount or 0))
    if not tx.has_category_lines or not tx.category_lines or not total:
        return [(tx.category, 1.0)]
    # A line cannot be saved in an ignored category, but the category can be
    # ignored while the split is being saved; leave that part out, as the
    # shared P/L filters do.
    return [
        (line.category, float(line.amount) / total)
        for line in tx.category_lines
        if not (line.category and line.category.is_ignored)
    ]


def _is_transfer_part(tx: Transaction, category: Optional[Category]) -> bool:
    return (
        bool(tx.transfer_pair_id)
        or tx.source == "transfer"
        or bool(category and category.treat_as_transfer)
    )


def _is_transfer_item(tx: Transaction, parts: list[tuple[Optional[Category], float]]) -> bool:
    """The row reads as a transfer only when all of it is one."""
    return bool(tx.transfer_pair_id) or all(
        bool(category and category.treat_as_transfer) for category, _ in parts
    )


def _activity_date(
    tx: Transaction,
    date_basis: Literal["reporting", "bank"],
    accounting_mode: str,
) -> date:
    if date_basis == "bank":
        return tx.date
    return reporting_date_value(tx, accounting_mode)


def _actual_item(
    tx: Transaction,
    activity_date: date,
    amount_primary: float,
    is_transfer: bool,
    ignored: bool,
) -> TransactionCalendarItem:
    category = tx.category
    account = tx.account
    return TransactionCalendarItem(
        kind="actual",
        id=tx.id,
        date=activity_date,
        description=tx.description,
        amount=float(tx.amount),
        amount_primary=amount_primary,
        currency=tx.currency,
        type=cast(Literal["debit", "credit"], tx.type),
        account_id=tx.account_id,
        account_name=account.display_name or account.name if account else None,
        category_id=tx.category_id,
        category_name=category.name if category else None,
        category_icon=category.icon if category else None,
        category_color=category.color if category else None,
        status=tx.status,
        source=tx.source,
        transfer_pair_id=tx.transfer_pair_id,
        is_transfer=is_transfer,
        is_ignored=ignored,
        has_category_lines=bool(tx.has_category_lines),
        exclude_from_pnl=bool(tx.exclude_from_pnl),
    )


def _forecast_item(
    tx: Transaction,
    activity_date: date,
    amount_primary: float,
    is_transfer: bool,
    ignored: bool,
) -> TransactionCalendarItem:
    category = tx.category
    account = tx.account
    return TransactionCalendarItem(
        kind="projected",
        id=tx.id,
        date=activity_date,
        description=tx.description,
        amount=float(tx.amount),
        amount_primary=amount_primary,
        currency=tx.currency,
        type=cast(Literal["debit", "credit"], tx.type),
        account_id=tx.account_id,
        account_name=account.display_name or account.name if account else None,
        category_id=tx.category_id,
        category_name=category.name if category else None,
        category_icon=category.icon if category else None,
        category_color=category.color if category else None,
        status=tx.status,
        source=tx.source,
        transfer_pair_id=tx.transfer_pair_id,
        is_transfer=is_transfer,
        is_ignored=ignored,
        has_category_lines=bool(tx.has_category_lines),
        exclude_from_pnl=bool(tx.exclude_from_pnl),
    )
