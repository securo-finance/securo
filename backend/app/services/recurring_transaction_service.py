import calendar
import uuid
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today, get_workspace_timezone, today_in
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import recurring_match_service
from app.services.business_days import (
    next_banking_day,
    nth_business_day,
    previous_banking_day,
)
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import stamp_primary_amount


async def _verify_account_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    """Raise ValueError if the account isn't reachable from this workspace."""
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Account not found")


async def get_recurring_transactions(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[RecurringTransaction]:
    result = await session.execute(
        select(RecurringTransaction)
        .where(RecurringTransaction.workspace_id == workspace_id)
        .order_by(RecurringTransaction.next_occurrence)
    )
    return list(result.scalars().all())


async def get_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[RecurringTransaction]:
    result = await session.execute(
        select(RecurringTransaction)
        .where(RecurringTransaction.id == recurring_id, RecurringTransaction.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def create_recurring_transaction(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: RecurringTransactionCreate,
) -> RecurringTransaction:
    await _verify_account_in_workspace(session, workspace_id, data.account_id)
    _validate_rules(data.frequency, data.business_day)
    intended_day = data.day_of_month or data.start_date.day
    rules = {
        "weekend_adjustment": data.weekend_adjustment,
        "business_day": data.business_day,
        "count_saturdays": data.count_saturdays,
        "holiday_calendar": data.holiday_calendar,
    }
    next_occ = _first_pointer(data.start_date, data.frequency, intended_day, rules)
    if data.skip_first:
        next_occ = _advance_date(next_occ, data.frequency, intended_day=intended_day)
    recurring = RecurringTransaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=data.account_id,
        category_id=data.category_id,
        description=data.description,
        amount=data.amount,
        currency=data.currency,
        type=data.type,
        frequency=data.frequency,
        weekend_adjustment=data.weekend_adjustment,
        day_of_month=data.day_of_month,
        business_day=data.business_day,
        count_saturdays=data.count_saturdays,
        holiday_calendar=data.holiday_calendar,
        grace_days=data.grace_days,
        match_keywords=data.match_keywords,
        amount_tolerance_percent=data.amount_tolerance_percent,
        start_date=data.start_date,
        end_date=data.end_date,
        auto_generate=data.auto_generate,
        next_occurrence=next_occ,
    )
    session.add(recurring)
    await session.flush()
    await stamp_primary_amount(
        session, user_id, recurring,
        date_field="start_date",
    )
    await session.commit()
    await session.refresh(recurring)
    return recurring


async def update_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID, data: RecurringTransactionUpdate
) -> Optional[RecurringTransaction]:
    recurring = await get_recurring_transaction(session, recurring_id, workspace_id)
    if not recurring:
        return None

    update_data = data.model_dump(exclude_unset=True)

    for required in (
        "weekend_adjustment", "start_date", "frequency", "count_saturdays", "grace_days",
        "amount_tolerance_percent",
    ):
        if required in update_data and update_data[required] is None:
            raise ValueError(f"{required} is required")

    # A recurring transaction must always have an account — reject an explicit
    # null, and verify ownership of any new account_id.
    if "account_id" in update_data:
        new_account_id = update_data["account_id"]
        if new_account_id is None:
            raise ValueError("account_id is required")
        if new_account_id != recurring.account_id:
            await _verify_account_in_workspace(session, workspace_id, new_account_id)

    schedule_changed = any(
        key in update_data and update_data[key] != getattr(recurring, key)
        for key in _SCHEDULE_FIELDS
    )
    previous_next_occurrence = recurring.next_occurrence

    _validate_rules(
        update_data.get("frequency", recurring.frequency),
        update_data.get("business_day", recurring.business_day),
    )

    money_changed = any(
        key in update_data and update_data[key] != getattr(recurring, key)
        for key in ("amount", "currency")
    )

    for key, value in update_data.items():
        setattr(recurring, key, value)

    if money_changed:
        # The stamped primary amount converts the old figure; keeping it would
        # project the old value. Cleared first so a currency with no rate yet
        # falls back to the native amount instead of the stale conversion.
        recurring.amount_primary = None
        recurring.fx_rate_used = None
        await stamp_primary_amount(
            session, recurring.user_id, recurring, date_field="start_date",
        )

    if schedule_changed:
        intended_day = recurring.day_of_month or recurring.start_date.day
        recurring.next_occurrence = _first_occurrence_on_or_after(
            _first_pointer(
                recurring.start_date, recurring.frequency, intended_day,
                occurrence_rules(recurring),
            ),
            recurring.frequency,
            intended_day=intended_day,
            floor=previous_next_occurrence,
        )

    await session.commit()
    await session.refresh(recurring)
    return recurring


async def delete_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    recurring = await get_recurring_transaction(session, recurring_id, workspace_id)
    if not recurring:
        return False

    await session.delete(recurring)
    await session.commit()
    return True


def _advance_months(current: date, months: int, intended_day: int) -> date:
    """Advance by calendar months, clamping only in a shorter target month."""
    month_index = current.month - 1 + months
    year = current.year + month_index // 12
    month = month_index % 12 + 1
    day = min(intended_day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _advance_date(
    current: date, frequency: str, intended_day: Optional[int] = None,
) -> date:
    """Advance a date by the given frequency.

    For monthly, quarterly, semiannual, and yearly recurrences, ``intended_day`` is the day
    the user actually wants (e.g. 31). We cap it to the target month's length
    so short months clamp, but subsequent occurrences recover to the intended
    day when it exists again. Falls back to ``current.day`` when not provided.
    """
    if frequency == "weekly":
        return current + timedelta(weeks=1)
    if frequency == "biweekly":
        return current + timedelta(weeks=2)

    target_day = intended_day if intended_day else current.day
    if frequency == "monthly":
        return _advance_months(current, 1, target_day)
    if frequency == "quarterly":
        return _advance_months(current, 3, target_day)
    if frequency == "semiannual":
        return _advance_months(current, 6, target_day)
    if frequency == "yearly":
        year = current.year + 1
        day = min(target_day, calendar.monthrange(year, current.month)[1])
        return date(year, current.month, day)

    # Preserve the existing monthly fallback for unknown legacy values.
    return _advance_months(current, 1, target_day)


# Fields the occurrence schedule is derived from; changing any of them moves
# the next_occurrence pointer.
_SCHEDULE_FIELDS = (
    "start_date", "day_of_month", "frequency",
    "business_day", "count_saturdays", "holiday_calendar",
)

_MONTH_BASED_FREQUENCIES = ("monthly", "quarterly", "semiannual", "yearly")


def _validate_rules(frequency: str, business_day: Optional[int]) -> None:
    """A business-day rule names a day of a month, so it needs months."""
    if business_day is not None and frequency not in _MONTH_BASED_FREQUENCIES:
        raise ValueError(
            "business_day requires a monthly, quarterly, semiannual or yearly frequency"
        )


def occurrence_rules(recurring) -> dict:
    """The fields that turn a nominal pointer into the day money moves.

    Passed as keyword arguments to ``adjust_weekend_date`` and
    ``get_occurrences_in_range``, so every caller derives the same date from
    the same row.
    """
    return {
        "weekend_adjustment": recurring.weekend_adjustment or "none",
        "business_day": recurring.business_day,
        "count_saturdays": bool(recurring.count_saturdays),
        "holiday_calendar": recurring.holiday_calendar,
    }


def effective_date_for(recurring, nominal: date) -> date:
    """The day this recurring's occurrence at ``nominal`` is actually due."""
    return adjust_weekend_date(nominal, **occurrence_rules(recurring))


def nearest_occurrence(recurring, when: date) -> tuple[date, date]:
    """The occurrence, from the pointer onward, whose due date is nearest ``when``.

    Returns ``(nominal, effective)``. On a tie the earlier occurrence wins: a
    charge exactly between two is more likely the late one than the early
    one. Lateness within ``grace_days`` does not count toward the distance,
    because the bill says a charge that late is still that occurrence: with
    fifteen days of grace, a charge on 16 Feb pays the bill due 1 Feb, not the
    one due 1 Mar that is two days closer. Occurrences past ``end_date`` are
    not considered; when none is left the pointer's own occurrence is
    returned.
    """
    grace = int(recurring.grace_days or 0)

    def distance(effective: date) -> int:
        late = (when - effective).days
        return max(0, late - grace) if late > 0 else -late

    intended_day = recurring.day_of_month or recurring.start_date.day
    pointer = recurring.next_occurrence
    best = (pointer, effective_date_for(recurring, pointer))
    for _ in range(600):
        if recurring.end_date and pointer > recurring.end_date:
            break
        effective = effective_date_for(recurring, pointer)
        if distance(effective) < distance(best[1]):
            best = (pointer, effective)
        if effective >= when:
            break
        pointer = _advance_date(pointer, recurring.frequency, intended_day=intended_day)
    return best


def _first_pointer(start: date, frequency: str, intended_day: int, rules: dict) -> date:
    """The schedule's first nominal occurrence, given it starts on ``start``.

    A month-based schedule falls on ``intended_day``, so when the start date
    is another day (start 1 Aug, day 17) the first occurrence is the 17th, not
    the 1st: putting it on the start date made it due on a day nothing is
    charged, and no charge on the 17th could ever be matched to it. An
    occurrence whose effective date would land before ``start`` (the 5th
    business day, for a schedule starting on the 20th) is not one either.
    """
    pointer = start
    if frequency in _MONTH_BASED_FREQUENCIES:
        day = min(intended_day, calendar.monthrange(start.year, start.month)[1])
        pointer = date(start.year, start.month, day)
        if pointer < start:
            pointer = _advance_date(pointer, frequency, intended_day=intended_day)
    if not rules.get("business_day"):
        # A weekend adjustment moving the first occurrence a day or two
        # before the start date is still that occurrence.
        return pointer
    guard = 0
    while adjust_weekend_date(pointer, **rules) < start and guard < 24:
        pointer = _advance_date(pointer, frequency, intended_day=intended_day)
        guard += 1
    return pointer


def _first_occurrence_on_or_after(
    start: date, frequency: str, intended_day: Optional[int], floor: date,
) -> date:
    """Return the first occurrence of the schedule on or after ``floor``.

    The schedule starts at ``start``, as on creation. ``floor`` is the pointer
    before the edit: every occurrence before it was already generated or
    matched, so the new pointer never moves behind it. That keeps an edit from
    backfilling past periods or repeating one that was already charged, while a
    ``start`` later than ``floor`` still defers the rule to ``start``.
    """
    current = start
    while current < floor:
        current = _advance_date(current, frequency, intended_day=intended_day)
    return current


def adjust_weekend_date(
    nominal_date: date,
    weekend_adjustment: str = "none",
    business_day: Optional[int] = None,
    count_saturdays: bool = False,
    holiday_calendar: Optional[str] = None,
) -> date:
    """Return the effective date without changing the nominal schedule date.

    With ``business_day`` the occurrence is the n-th business day of the
    nominal date's month; only the month of the pointer matters then. The
    result is moved off a day no money moves on by ``weekend_adjustment``:
    a weekend, or with ``holiday_calendar`` a holiday too. Without a holiday
    calendar, "previous_friday" and "next_monday" are exactly what they say.
    """
    if weekend_adjustment not in ("none", "previous_friday", "next_monday"):
        raise ValueError(f"Unsupported weekend adjustment: {weekend_adjustment}")

    day = nominal_date
    if business_day:
        day = nth_business_day(
            nominal_date.year, nominal_date.month, business_day,
            holiday_calendar=holiday_calendar, count_saturdays=count_saturdays,
        )
    if weekend_adjustment == "previous_friday":
        return previous_banking_day(day, holiday_calendar)
    if weekend_adjustment == "next_monday":
        return next_banking_day(day, holiday_calendar)
    return day


def _scan_margin(business_day: Optional[int], holiday_calendar: Optional[str]) -> int:
    """How far an effective date can sit from its nominal one, in days."""
    if business_day:
        # The pointer can be anywhere in the month, and the effective day too.
        return 35
    if holiday_calendar:
        # A weekend next to a holiday (Good Friday, Carnival) moves further.
        return 6
    return 2


def get_occurrences_in_range(
    start: date, frequency: str, end_date: Optional[date],
    range_start: date, range_end: date,
    intended_day: Optional[int] = None,
    weekend_adjustment: str = "none",
    business_day: Optional[int] = None,
    count_saturdays: bool = False,
    holiday_calendar: Optional[str] = None,
) -> list[date]:
    """Compute effective occurrence dates within ``[range_start, range_end)``.

    Schedule advancement and end-date checks use nominal dates. The scan
    margin captures nominal occurrences whose effective date moves across a
    range boundary; filtering happens only after calculating each effective
    date.
    """
    day = intended_day if intended_day else start.day
    margin = timedelta(days=_scan_margin(business_day, holiday_calendar))
    nominal_range_start = range_start - margin
    nominal_range_end = range_end + margin
    occurrences: list[date] = []
    current = start
    while current < nominal_range_start:
        if end_date and current > end_date:
            return occurrences
        current = _advance_date(current, frequency, intended_day=day)
    while current < nominal_range_end:
        if end_date and current > end_date:
            break
        effective_date = adjust_weekend_date(
            current, weekend_adjustment,
            business_day=business_day, count_saturdays=count_saturdays,
            holiday_calendar=holiday_calendar,
        )
        if range_start <= effective_date < range_end:
            occurrences.append(effective_date)
        current = _advance_date(current, frequency, intended_day=day)
        if len(occurrences) > 200:
            break
    return occurrences


async def generate_pending(
    session: AsyncSession,
    user_id: uuid.UUID,
    up_to: Optional[date] = None,
    include_manual: bool = False,
    workspace_id: Optional[uuid.UUID] = None,
) -> int:
    """Generate transactions for all pending recurring transactions up to a given date.
    If up_to is None, defaults to today. This allows the dashboard to pre-generate
    transactions for future months when the user navigates ahead.

    Schedules with auto_generate off are left alone unless include_manual is
    set: the hourly task must never materialize them, while the Generate
    Pending button is the user asking for exactly that.

    workspace_id narrows the run to one workspace. The button passes the
    workspace it was authorized for, so a click there cannot write into
    another workspace the user merely belongs to; unattended runs leave it
    unset and cover all of them.
    Returns the count of transactions generated."""
    # Unattended runs only materialize schedules that opted in to
    # auto-generation. The others are still visited, to link a real charge
    # that already arrived for a due occurrence: without that, a charge the
    # sync could not place (a late salary, a bill whose pointer fell behind)
    # left the occurrence projected forever next to the charge that paid it.
    generation_filter = true()
    if workspace_id is not None:
        generation_filter = RecurringTransaction.workspace_id == workspace_id
    # A person's recurring rows may live in several workspaces, and each
    # workspace keeps its own calendar, so "today" is resolved per workspace.
    # The query is bounded by the latest of those days and the loop below
    # applies each row's own cutoff.
    cutoffs: dict[uuid.UUID, date] = {}
    if up_to is None:
        workspace_ids = (
            await session.execute(
                select(RecurringTransaction.workspace_id)
                .where(
                    RecurringTransaction.user_id == user_id,
                    RecurringTransaction.is_active == True,
                    generation_filter,
                )
                .distinct()
            )
        ).scalars().all()
        for ws_id in workspace_ids:
            cutoffs[ws_id] = today_in(await get_workspace_timezone(session, ws_id))
        if not cutoffs:
            return 0
        latest_cutoff = max(cutoffs.values())
    else:
        latest_cutoff = up_to

    result = await session.execute(
        select(RecurringTransaction)
        .where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.is_active == True,
            generation_filter,
            # An effective date can sit before its nominal pointer (a Friday
            # before a weekend, the 5th business day of a month whose pointer
            # is on the 20th), so the query reaches past the cutoff by the
            # widest such gap and the loop applies the exact date.
            RecurringTransaction.next_occurrence <= latest_cutoff + timedelta(days=35),
        )
    )
    recurring_list = list(result.scalars().all())

    count = 0
    for recurring in recurring_list:
        # Legacy rows may exist with a null account_id from before account_id
        # was required. Skip them rather than crashing on Transaction's NOT NULL
        # constraint — the user should edit the recurring to fix it.
        if recurring.account_id is None:
            continue
        cutoff = up_to or cutoffs.get(recurring.workspace_id) or app_today()
        if not (include_manual or recurring.auto_generate):
            await _link_due_charges(session, recurring, cutoff)
            continue
        # Generate while the effective date is due. The nominal pointer remains
        # authoritative and is the only date used for schedule advancement and
        # end-date evaluation.
        while True:
            effective_occurrence = effective_date_for(
                recurring, recurring.next_occurrence
            )
            if effective_occurrence > cutoff:
                break
            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False
                break

            # If a real transaction (synced/imported/manual) already covers this
            # occurrence, link it to the bill instead of writing a duplicate
            # placeholder (issue #116). Otherwise materialize the placeholder,
            # stamped with the recurring link so a later synced charge merges
            # into it rather than duplicating.
            existing_real = await recurring_match_service.find_real_tx_for_occurrence(
                session, recurring, effective_occurrence
            )
            if existing_real is not None:
                existing_real.recurring_transaction_id = recurring.id
            else:
                account = await session.get(Account, recurring.account_id)
                # Only occurrences that already came due reach this point, so
                # the row is a charge that happened rather than a forecast.
                # Whether to trust that depends on who else writes to the
                # account:
                #
                # Bank-synced: the incoming charge often fails to match this
                # placeholder, and two posted rows for one charge inflate the
                # balance silently. Holding the placeholder as pending keeps
                # it out of the actuals until something confirms it, so a
                # missed match costs a visible stale row instead of a wrong
                # number. Improving the match itself is the real fix (#588);
                # this is the guard until then.
                #
                # Manual: nothing else ever writes to the account, so there is
                # no charge to duplicate against and nothing that would ever
                # post the row. Pending there buys no safety and stops the
                # balance from moving.
                is_synced_account = account is not None and account.connection_id is not None
                transaction = Transaction(
                    user_id=user_id,
                    account_id=recurring.account_id,
                    category_id=recurring.category_id,
                    description=recurring.description,
                    amount=recurring.amount,
                    currency=recurring.currency,
                    date=effective_occurrence,
                    type=recurring.type,
                    source="recurring",
                    status="pending" if is_synced_account else "posted",
                    recurring_transaction_id=recurring.id,
                )
                apply_effective_date(transaction, account)
                session.add(transaction)
                await session.flush()
                await stamp_primary_amount(session, user_id, transaction)
                count += 1

            # Advance to next occurrence
            recurring.next_occurrence = _advance_date(
                recurring.next_occurrence, recurring.frequency,
                intended_day=recurring.day_of_month or recurring.start_date.day,
            )

            # Check again if past end_date after advancing
            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False

    await session.commit()
    return count


async def _link_due_charges(
    session: AsyncSession, recurring: RecurringTransaction, cutoff: date
) -> None:
    """Link real charges to the due occurrences of a bill that is not generated.

    No placeholder is ever written here. Each due occurrence is looked up on
    its own, and the pointer moves only past the last one a charge answered:
    an occurrence nothing paid stays projected, so an unpaid bill is still
    visible, unless a later occurrence was paid, which settles that the
    schedule moved on.
    """
    intended_day = recurring.day_of_month or recurring.start_date.day
    pointer = recurring.next_occurrence
    last_linked: Optional[date] = None
    for _ in range(60):
        if recurring.end_date and pointer > recurring.end_date:
            break
        effective = effective_date_for(recurring, pointer)
        if effective > cutoff:
            break
        real = await recurring_match_service.find_real_tx_for_occurrence(
            session, recurring, effective
        )
        if real is not None:
            real.recurring_transaction_id = recurring.id
            await session.flush()
            last_linked = pointer
        pointer = _advance_date(pointer, recurring.frequency, intended_day=intended_day)
    if last_linked is None:
        return
    recurring.next_occurrence = _advance_date(
        last_linked, recurring.frequency, intended_day=intended_day
    )
    if recurring.end_date and recurring.next_occurrence > recurring.end_date:
        recurring.is_active = False
