"""Daily projected balance: what the money will be on each day ahead.

``load_projection_base`` gathers every known balance move (posted rows,
forecast rows and virtual recurring occurrences) into a seed and per-day
deltas. ``walk`` turns that base into a daily balance, optionally spending a
daily allowance on every day from today on. The calendar and the spending
plan both read balances through here, so the two never disagree.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.transaction_calendar import TransactionCalendarItem
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.credit_card_service import compute_effective_date
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


RecurringItem = tuple[TransactionCalendarItem, float, list[tuple[bool, float]]]


@dataclass(frozen=True)
class ProjectionBase:
    """Every known balance move on ``[start, end)``, before any daily allowance."""

    start: date
    end: date
    # Balance at the end of ``start - 1``, carrying forecast rows and overdue
    # recurring occurrences that fall between today and ``start``.
    seed: float
    deltas: dict[date, float]
    # The virtual recurring occurrences behind the recurring deltas, for
    # callers that list them: (item, signed primary delta, parts), where each
    # part is (is_transfer, share of the template).
    recurring_items: list[RecurringItem]


@dataclass(frozen=True)
class DayPoint:
    date: date
    balance: float
    allowance: float


def is_ignored_row(tx: Transaction) -> bool:
    return bool(tx.is_ignored or (tx.category and tx.category.is_ignored))


async def load_projection_base(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
) -> ProjectionBase:
    today = app_today()
    seed = await _balance_at(
        session,
        workspace_id,
        start - timedelta(days=1),
        primary_currency_hint=primary_currency,
        account_ids=account_ids,
        include_pending=True,
    )
    # A range that starts after today begins past the current balance. Carry
    # real forecast rows that fall in the gap into the seed, just like virtual
    # recurring occurrences are carried below.
    if start > today:
        for tx in await _get_forecast_transactions(
            session, workspace_id, today + timedelta(days=1), start, account_ids
        ):
            if not is_ignored_row(tx):
                seed += await signed_balance_delta_primary(session, tx, primary_currency)

    deltas = await _daily_balance_deltas_by_date(
        session,
        workspace_id,
        start,
        end,
        primary_currency_hint=primary_currency,
        account_ids=account_ids,
    )
    for tx in await _get_forecast_transactions(
        session, workspace_id, start, end, account_ids
    ):
        # Forecast rows are excluded from the posted deltas but belong to the
        # projected walk.
        if not is_ignored_row(tx):
            deltas[tx.date] = deltas.get(tx.date, 0.0) + await signed_balance_delta_primary(
                session, tx, primary_currency
            )

    recurring_items, recurring_deltas, carried = await project_recurring_items(
        session, workspace_id, primary_currency, start, end, account_ids
    )
    for delta_date, delta in recurring_deltas.items():
        deltas[delta_date] = deltas.get(delta_date, 0.0) + delta

    if await get_credit_card_accounting_mode(session) == "accrual":
        seed += await _move_card_charges_to_due_dates(
            session, workspace_id, primary_currency, start, end, account_ids, deltas, recurring_items
        )

    return ProjectionBase(
        start=start,
        end=end,
        seed=seed + carried,
        deltas=deltas,
        recurring_items=recurring_items,
    )


def _moves_on_due_date(tx: Transaction) -> bool:
    """A card charge leaves the money on its bill's due date. A bill payment
    (a transfer leg) or an opening balance moves the money on its own date."""
    if tx.transfer_pair_id or tx.source in ("transfer", "opening_balance"):
        return False
    return not (tx.category and tx.category.treat_as_transfer)


async def _move_card_charges_to_due_dates(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
    deltas: dict[date, float],
    recurring_items: list[RecurringItem],
) -> float:
    """Re-date card charges from their purchase date to their bill's due date.

    The app's accrual mode (Settings) says a card purchase reaches the money
    when its bill is due, and the dashboard already buckets it that way. The
    walk is built from purchase dates, so this moves each card charge that
    crosses into or within ``[start, end)``: out of the seed or its purchase
    day, into its due day. Real rows carry their due date in
    ``effective_date`` (cycle math, the bank's bill, or a manual override);
    virtual recurring occurrences get it from the card's cycle days.

    Mutates ``deltas`` and returns the change to the seed.
    """
    seed_change = 0.0

    def place(day: date, amount: float) -> None:
        nonlocal seed_change
        if day < start:
            seed_change += amount
        elif day < end:
            deltas[day] = deltas.get(day, 0.0) + amount

    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.type == "credit_card",
            Account.is_closed == False,  # noqa: E712
            Transaction.is_ignored == False,  # noqa: E712
            or_(Transaction.category_id.is_(None), Category.is_ignored == False),  # noqa: E712
            Transaction.date < end,
            or_(Transaction.date >= start, Transaction.effective_date >= start),
            Transaction.effective_date != Transaction.date,
        )
        .options(selectinload(Transaction.account), selectinload(Transaction.category))
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    for tx in (await session.execute(stmt)).scalars():
        if not _moves_on_due_date(tx):
            continue
        amount = await signed_balance_delta_primary(session, tx, primary_currency)
        place(tx.date, -amount)
        place(tx.effective_date, amount)

    cards = {
        account.id: account
        for account in (await session.execute(
            select(Account).where(
                Account.workspace_id == workspace_id,
                Account.type == "credit_card",
                *([Account.id.in_(account_ids)] if account_ids is not None else []),
            )
        )).scalars()
    }
    for item, signed_delta, _parts in recurring_items:
        card = cards.get(item.account_id) if item.account_id else None
        if card is None or item.is_ignored or item.is_transfer:
            continue
        due = compute_effective_date(item.date, card.statement_close_day, card.payment_due_day)
        if due != item.date:
            place(item.date, -signed_delta)
            place(due, signed_delta)
    return seed_change


def allowance_on(day: date, today: date, allowance: float, spent_today: float) -> float:
    """What the daily allowance takes from the balance on ``day``.

    Past days spent what they spent. Today spends what is left of today's
    allowance after the variable spending already recorded; an overspent
    today takes nothing more, its excess already sits in the balance.
    """
    if day < today:
        return 0.0
    if day == today:
        return max(allowance - spent_today, 0.0)
    return allowance


def walk(
    base: ProjectionBase,
    today: date,
    allowance: float = 0.0,
    spent_today: float = 0.0,
) -> list[DayPoint]:
    running = base.seed
    if base.start > today:
        # Days between today and the range start spent their allowance too.
        gap_days = (base.start - today).days - 1
        running -= allowance_on(today, today, allowance, spent_today) + allowance * gap_days
    points: list[DayPoint] = []
    for day in date_range(base.start, base.end):
        spend = allowance_on(day, today, allowance, spent_today)
        running += base.deltas.get(day, 0.0) - spend
        points.append(DayPoint(date=day, balance=round(running, 2), allowance=spend))
    return points


def _stays_above(points: list[DayPoint], today: date, floor: float) -> bool:
    return all(p.balance >= floor for p in points if p.date >= today)


def safe_daily_allowance(
    base: ProjectionBase, today: date, floor: float, spent_today: float = 0.0
) -> float:
    """The largest daily allowance that keeps every day ahead at or above ``floor``.

    A larger allowance lowers every future balance, so feasibility is monotone
    and a binary search over cents finds the exact value the walk agrees with.
    """
    if not _stays_above(walk(base, today, 0.0, spent_today), today, floor):
        return 0.0
    low = 0
    high = int(max(0.0, base.seed - floor) * 100) + sum(
        int(abs(d) * 100) for d in base.deltas.values()
    ) + int(spent_today * 100) + 100
    while low < high:
        middle = (low + high + 1) // 2
        if _stays_above(walk(base, today, middle / 100, spent_today), today, floor):
            low = middle
        else:
            high = middle - 1
    return low / 100


def covered_until(points: list[DayPoint], today: date, floor: float) -> Optional[date]:
    """The last day ahead whose balance stays at or above ``floor`` without a dip before it.

    None when today is already below the floor.
    """
    last_covered: Optional[date] = None
    for point in points:
        if point.date < today:
            continue
        if point.balance < floor:
            return last_covered
        last_covered = point.date
    return last_covered


def date_range(start: date, end: date):
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


async def positive_primary_amount(
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


async def signed_balance_delta_primary(
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


async def project_recurring_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
    account_ids: Optional[list[uuid.UUID]],
) -> tuple[list[RecurringItem], dict[date, float], float]:
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

    items: list[RecurringItem] = []
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
        amount_primary = await positive_primary_amount(
            session, rec.amount, rec.currency, primary_currency, rec.amount_primary
        )
        signed_delta = amount_primary if rec.type == "credit" else -amount_primary
        is_transfer = bool(category and category.treat_as_transfer)
        is_ignored = bool(category and category.is_ignored)
        template_total = abs(float(rec.amount or 0))
        if rec.has_category_lines and rec.category_lines and template_total:
            parts = [
                (bool(line.category and line.category.treat_as_transfer), float(line.amount) / template_total)
                for line in rec.category_lines
                if not (line.category and line.category.is_ignored)
            ]
            is_transfer = all(part_is_transfer for part_is_transfer, _ in parts)
        else:
            parts = [(is_transfer, 1.0)]
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
                has_category_lines=bool(rec.has_category_lines),
            )
            items.append((item, signed_delta, parts))
            # The row is still listed, but an ignored occurrence never moves the
            # projected balance: _daily_balance_deltas_by_date already leaves the
            # posted version out, so counting the projection here would promise a
            # balance that reverts the day the recurring actually posts.
            if not is_ignored:
                deltas[occ_date] = deltas.get(occ_date, 0.0) + signed_delta
    return items, deltas, carried_delta
