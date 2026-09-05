import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction_calendar import (
    TransactionCalendarDay,
    TransactionCalendarItem,
    TransactionCalendarResponse,
)
from app.services.dashboard_service import (
    _balance_at,
    _daily_balance_deltas_by_date,
    _get_forecast_transactions,
)
from app.services.fx_rate_service import convert as fx_convert
from app.services.recurring_transaction_service import (
    adjust_weekend_date,
    get_occurrences_in_range,
)


async def get_transaction_calendar(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> TransactionCalendarResponse:
    """Build a read-only month-grid transaction calendar.

    The calendar shows posted/manual transactions plus virtual recurring
    occurrences. Virtual rows are never persisted; they only affect the daily
    projected ending balance returned by this read endpoint.
    """
    if month is None:
        month = date.today().replace(day=1)
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
        for d in _date_range(grid_start, grid_end)
    }

    start_balance = await _balance_at(
        session,
        workspace_id,
        grid_start - timedelta(days=1),
        primary_currency_hint=primary_currency,
        account_ids=requested_account_ids,
        include_pending=True,
    )

    # A future calendar month starts after today's current balance. Carry real
    # forecast rows that fall in the gap into the projected seed, just like
    # virtual recurring occurrences are carried below.
    if grid_start > date.today():
        before_grid_rows = await _get_forecast_transactions(
            session, workspace_id, date.today() + timedelta(days=1), grid_start,
            requested_account_ids,
        )
        for tx in before_grid_rows:
            if tx.is_ignored or (tx.category and tx.category.is_ignored):
                continue
            start_balance += await _signed_balance_delta_primary(
                session, tx, primary_currency
            )

    actual_rows = await _load_actual_transactions(
        session, workspace_id, grid_start, grid_end, requested_account_ids
    )
    balance_deltas = await _daily_balance_deltas_by_date(
        session,
        workspace_id,
        grid_start,
        grid_end,
        primary_currency_hint=primary_currency,
        account_ids=requested_account_ids,
    )
    for tx in actual_rows:
        if tx.date not in days:
            continue
        day = days[tx.date]
        amount_primary = await _positive_primary_amount(
            session, tx.amount, tx.currency, primary_currency, tx.amount_primary
        )

        is_transfer = bool(tx.transfer_pair_id) or bool(
            tx.category and tx.category.treat_as_transfer
        )
        ignored = bool(tx.is_ignored or (tx.category and tx.category.is_ignored))
        if not ignored and not tx.exclude_from_pnl:
            if is_transfer or tx.source == "transfer":
                transfer_delta = await _signed_balance_delta_primary(session, tx, primary_currency)
                day.transfer_net += transfer_delta
                day.actual_transfer_net += transfer_delta
                day.has_transfer = True
            elif tx.source != "opening_balance":
                if tx.type == "credit":
                    day.income += amount_primary
                    day.actual_income += amount_primary
                    day.has_income = True
                else:
                    day.expense += amount_primary
                    day.actual_expense += amount_primary
                    day.has_expense = True

        day.actual_count += 1
        day.items.append(_actual_item(tx, amount_primary, is_transfer, ignored))

    forecast_rows = await _get_forecast_transactions(
        session, workspace_id, grid_start, grid_end, requested_account_ids
    )
    forecast_deltas: dict[date, float] = {}
    for tx in forecast_rows:
        if tx.date not in days:
            continue
        day = days[tx.date]
        amount_primary = await _positive_primary_amount(
            session, tx.amount, tx.currency, primary_currency, tx.amount_primary
        )
        is_transfer = bool(tx.transfer_pair_id) or bool(
            tx.category and tx.category.treat_as_transfer
        )
        ignored = bool(tx.is_ignored or (tx.category and tx.category.is_ignored))
        if not ignored and not tx.exclude_from_pnl:
            if is_transfer or tx.source == "transfer":
                delta = await _signed_balance_delta_primary(session, tx, primary_currency)
                day.transfer_net += delta
                day.projected_transfer_net += delta
                day.has_transfer = True
            elif tx.type == "credit":
                day.income += amount_primary
                day.projected_income += amount_primary
                day.has_income = True
            else:
                day.expense += amount_primary
                day.projected_expense += amount_primary
                day.has_expense = True
        day.projected_count += 1
        day.items.append(_forecast_item(tx, amount_primary, is_transfer, ignored))

        # Forecast rows are excluded from actual deltas but included in the
        # projected walk. The seed already contains forecast rows before the
        # grid; rows inside the grid are applied here.
        if not ignored:
            forecast_deltas[tx.date] = forecast_deltas.get(tx.date, 0.0) + await _signed_balance_delta_primary(
                session, tx, primary_currency
            )

    projected_items, projected_deltas, carried_projected_delta = await _project_recurring_items(
        session,
        workspace_id,
        primary_currency,
        grid_start,
        grid_end,
        requested_account_ids,
    )
    start_balance += carried_projected_delta
    for item, signed_delta in projected_items:
        day = days[item.date]
        amount_primary = abs(signed_delta)
        # Ignored projections stay out of every aggregate, exactly like ignored
        # actuals: out of the activity buckets here, and out of the balance deltas
        # built in _project_recurring_items. A recurring the user chose to ignore
        # must not move a projected balance that will not move once it posts.
        if not item.is_ignored:
            if item.is_transfer:
                day.transfer_net += signed_delta
                day.projected_transfer_net += signed_delta
                day.has_transfer = True
            elif item.type == "credit":
                day.income += amount_primary
                day.projected_income += amount_primary
                day.has_income = True
            else:
                day.expense += amount_primary
                day.projected_expense += amount_primary
                day.has_expense = True
        day.projected_count += 1
        day.items.append(item)

    for delta_date, delta in projected_deltas.items():
        balance_deltas[delta_date] = balance_deltas.get(delta_date, 0.0) + delta
    for delta_date, delta in forecast_deltas.items():
        balance_deltas[delta_date] = balance_deltas.get(delta_date, 0.0) + delta

    running = start_balance
    response_days: list[TransactionCalendarDay] = []
    for d in _date_range(grid_start, grid_end):
        running += balance_deltas.get(d, 0.0)
        day = days[d]
        day.ending_balance = round(running, 2)
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
            for d in _date_range(grid_start, grid_end)
        ],
    )


def _next_month(month_start: date) -> date:
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def _date_range(start: date, end: date):
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


async def _load_actual_transactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.date <= date.today(),
            Transaction.status == "posted",
        )
        .options(
            selectinload(Transaction.account),
            selectinload(Transaction.category),
            selectinload(Transaction.payee_entity),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _positive_primary_amount(
    session: AsyncSession,
    amount: Decimal,
    currency: str,
    primary_currency: str,
    stamped_primary: Decimal | float | None = None,
) -> float:
    if currency == primary_currency:
        return float(abs(amount))
    if stamped_primary is not None:
        return float(abs(Decimal(str(stamped_primary))))
    converted, _ = await fx_convert(session, abs(amount), currency, primary_currency)
    return float(converted)


async def _signed_balance_delta_primary(
    session: AsyncSession,
    tx: Transaction,
    primary_currency: str,
) -> float:
    account_currency = tx.account.currency if tx.account else tx.currency
    effective = (
        tx.amount
        if tx.currency == account_currency
        else Decimal(str(tx.amount_primary or tx.amount))
    )
    amount = abs(Decimal(str(effective)))
    if account_currency != primary_currency:
        amount, _ = await fx_convert(session, amount, account_currency, primary_currency)
    signed = amount if tx.type == "credit" else -amount
    return float(signed)


def _actual_item(
    tx: Transaction, amount_primary: float, is_transfer: bool, ignored: bool
) -> TransactionCalendarItem:
    category = tx.category
    account = tx.account
    return TransactionCalendarItem(
        kind="actual",
        id=tx.id,
        date=tx.date,
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
        exclude_from_pnl=bool(tx.exclude_from_pnl),
    )


def _forecast_item(
    tx: Transaction, amount_primary: float, is_transfer: bool, ignored: bool
) -> TransactionCalendarItem:
    category = tx.category
    account = tx.account
    return TransactionCalendarItem(
        kind="projected",
        id=tx.id,
        date=tx.date,
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
        exclude_from_pnl=bool(tx.exclude_from_pnl),
    )


def _count_occurrences_before(recurring: RecurringTransaction, end: date) -> int:
    """Count still-virtual effective occurrences before ``end`` without truncating."""
    nominal_start = recurring.next_occurrence
    first_effective = adjust_weekend_date(
        nominal_start, recurring.weekend_adjustment
    )
    range_start = min(nominal_start, first_effective)
    range_end = end
    if recurring.end_date is not None:
        final_effective = adjust_weekend_date(
            recurring.end_date, recurring.weekend_adjustment
        )
        range_end = min(range_end, final_effective + timedelta(days=1))

    count = 0
    while range_start < range_end:
        # Weekly is the shortest supported cadence. Two hundred-week chunks
        # stay within the occurrence helper's collection limit while allowing
        # an arbitrarily long carry horizon.
        chunk_end = min(range_start + timedelta(weeks=200), range_end)
        occurrences = get_occurrences_in_range(
            start=nominal_start,
            frequency=recurring.frequency,
            end_date=recurring.end_date,
            range_start=range_start,
            range_end=chunk_end,
            intended_day=recurring.day_of_month or recurring.start_date.day,
            weekend_adjustment=recurring.weekend_adjustment,
        )
        count += len(occurrences)
        range_start = chunk_end
    return count


async def _project_recurring_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
) -> tuple[list[tuple[TransactionCalendarItem, float]], dict[date, float], float]:
    stmt = (
        select(RecurringTransaction)
        .join(Account, RecurringTransaction.account_id == Account.id)
        .outerjoin(Category, RecurringTransaction.category_id == Category.id)
        .where(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active == True,
            RecurringTransaction.start_date < end + timedelta(days=2),
            Account.is_closed == False,
        )
        .options(
            selectinload(RecurringTransaction.account),
            selectinload(RecurringTransaction.category),
        )
    )
    if account_ids is not None:
        stmt = stmt.where(RecurringTransaction.account_id.in_(account_ids))
    result = await session.execute(stmt)
    recurring_rows = list(result.scalars().all())

    items: list[tuple[TransactionCalendarItem, float]] = []
    deltas: dict[date, float] = {}
    carried_delta = 0.0
    for rec in recurring_rows:
        if rec.account_id is None:
            continue
        occurrences = get_occurrences_in_range(
            start=rec.next_occurrence,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=start,
            range_end=end,
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        )
        category = rec.category
        account = rec.account
        amount_primary = await _positive_primary_amount(
            session, rec.amount, rec.currency, primary_currency, rec.amount_primary
        )
        signed_delta = amount_primary if rec.type == "credit" else -amount_primary
        is_transfer = bool(category and category.treat_as_transfer)
        is_ignored = bool(category and category.is_ignored)
        if not is_ignored:
            carried_delta += _count_occurrences_before(rec, start) * signed_delta
        for occ_date in occurrences:
            item = TransactionCalendarItem(
                kind="projected",
                recurring_id=rec.id,
                date=occ_date,
                description=rec.description,
                amount=float(rec.amount),
                amount_primary=amount_primary,
                currency=rec.currency,
                type=cast(Literal["debit", "credit"], rec.type),
                account_id=rec.account_id,
                account_name=account.display_name or account.name if account else None,
                category_id=rec.category_id,
                category_name=category.name if category else None,
                category_icon=category.icon if category else None,
                category_color=category.color if category else None,
                source="recurring",
                is_transfer=is_transfer,
                is_ignored=is_ignored,
            )
            items.append((item, signed_delta))
            # The row is still listed, but an ignored occurrence never moves the
            # projected balance: _daily_balance_deltas_by_date already leaves the
            # posted version out, so counting the projection here would promise a
            # balance that reverts the day the recurring actually posts.
            if not is_ignored:
                deltas[occ_date] = deltas.get(occ_date, 0.0) + signed_delta
    return items, deltas, carried_delta
