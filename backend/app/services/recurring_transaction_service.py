import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today, get_workspace_timezone, today_in
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import (
    RecurringMonthSummary,
    RecurringTransactionCreate,
    RecurringTransactionUpdate,
)
from app.services import category_line_service, recurring_match_service
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import convert_or_none, stamp_primary_amount
from app.services.rule_service import apply_rules_to_transaction


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
    next_occ = data.start_date
    if data.skip_first:
        next_occ = _advance_date(
            data.start_date, data.frequency,
            intended_day=data.day_of_month or data.start_date.day,
        )
    if data.category_id and data.category_lines and data.category_lines.lines:
        raise ValueError("Choose either a category or a split across categories")
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
        start_date=data.start_date,
        end_date=data.end_date,
        auto_generate=data.auto_generate,
        next_occurrence=next_occ,
    )
    session.add(recurring)
    await session.flush()
    await category_line_service.replace_template_lines(session, recurring, data.category_lines)
    await stamp_primary_amount(
        session, user_id, recurring,
        date_field="start_date",
    )
    await session.commit()
    await session.refresh(recurring, ["category_lines"])
    await session.refresh(recurring)
    return recurring


async def update_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID, data: RecurringTransactionUpdate
) -> Optional[RecurringTransaction]:
    recurring = await get_recurring_transaction(session, recurring_id, workspace_id)
    if not recurring:
        return None

    update_data = data.model_dump(exclude_unset=True)
    # Same payload rules as a transaction: absent keeps the parts, a list
    # replaces them, an empty list undoes the split.
    lines_payload = data.category_lines if "category_lines" in update_data else None
    update_data.pop("category_lines", None)
    splitting = lines_payload is not None and bool(lines_payload.lines)
    unsplitting = lines_payload is not None and not lines_payload.lines
    if splitting and update_data.get("category_id") is not None:
        raise ValueError("Choose either a category or a split across categories")
    if (splitting or (recurring.has_category_lines and not unsplitting)) and "category_id" in update_data:
        if update_data["category_id"] is not None and not unsplitting:
            raise ValueError("Undo the split across categories before choosing a single category")
        update_data.pop("category_id")
    if unsplitting and update_data.get("category_id") is None:
        # The editor sends a null category alongside; the largest part's
        # category is what an undone split keeps.
        update_data.pop("category_id", None)
    old_amount = recurring.amount

    for required in ("weekend_adjustment", "start_date", "frequency"):
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

    if unsplitting:
        await category_line_service.replace_template_lines(session, recurring, lines_payload)
    for key, value in update_data.items():
        setattr(recurring, key, value)
    if splitting:
        await session.flush()
        await category_line_service.replace_template_lines(session, recurring, lines_payload)
    elif "amount" in update_data:
        # An amount edited without new parts resizes the split.
        await category_line_service.rescale_template_lines(session, recurring, old_amount)
    if {"type", "account_id"} & update_data.keys():
        # A debit turned card credit may now carry a transfer part the
        # statement cannot drop.
        await session.flush()
        await category_line_service.validate_template_lines(session, recurring)

    if schedule_changed:
        recurring.next_occurrence = _first_occurrence_on_or_after(
            recurring.start_date,
            recurring.frequency,
            intended_day=recurring.day_of_month or recurring.start_date.day,
            floor=previous_next_occurrence,
        )

    await session.commit()
    await session.refresh(recurring, ["category_lines"])
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
_SCHEDULE_FIELDS = ("start_date", "day_of_month", "frequency")


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
    nominal_date: date, weekend_adjustment: str = "none"
) -> date:
    """Return the effective date without changing the nominal schedule date."""
    if weekend_adjustment not in ("none", "previous_friday", "next_monday"):
        raise ValueError(f"Unsupported weekend adjustment: {weekend_adjustment}")

    weekday = nominal_date.weekday()
    if weekend_adjustment == "none" or weekday < calendar.SATURDAY:
        return nominal_date
    if weekend_adjustment == "previous_friday":
        return nominal_date - timedelta(days=weekday - calendar.FRIDAY)
    return nominal_date + timedelta(days=7 - weekday)


def get_occurrences_in_range(
    start: date, frequency: str, end_date: Optional[date],
    range_start: date, range_end: date,
    intended_day: Optional[int] = None,
    weekend_adjustment: str = "none",
) -> list[date]:
    """Compute effective occurrence dates within ``[range_start, range_end)``.

    Schedule advancement and end-date checks use nominal dates. The two-day
    scan margin captures nominal weekend occurrences that move across a range
    boundary; filtering happens only after calculating each effective date.
    """
    day = intended_day if intended_day else start.day
    nominal_range_start = range_start - timedelta(days=2)
    nominal_range_end = range_end + timedelta(days=2)
    occurrences: list[date] = []
    current = start
    while current < nominal_range_start:
        if end_date and current > end_date:
            return occurrences
        current = _advance_date(current, frequency, intended_day=day)
    while current < nominal_range_end:
        if end_date and current > end_date:
            break
        effective_date = adjust_weekend_date(current, weekend_adjustment)
        if range_start <= effective_date < range_end:
            occurrences.append(effective_date)
        current = _advance_date(current, frequency, intended_day=day)
        if len(occurrences) > 200:
            break
    return occurrences


async def _charged_occurrences(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    month_start: date,
    month_end: date,
) -> dict[uuid.UUID, set[date]]:
    """The month's occurrences each stopped definition charged.

    A linked charge belongs to the occurrence it fulfills, not to the month
    of its own date: generation links a charge up to the policy's reach
    before or after the occurrence, so a December 1 occurrence can be paid
    by a November 30 charge and must still count in December.

    Charges are handed out as generation links them: occurrences in date
    order, each taking the nearest charge in its reach that an earlier one
    did not take. A charge goes to the first occurrence that reached it, even
    when a later one sits nearer, because generation never got that far.
    """
    stopped = (await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active == False,
        )
    )).scalars().all()
    if not stopped:
        return {}
    reach = {
        frequency: await recurring_match_service.occurrence_reach(
            session, workspace_id, frequency
        )
        for frequency in {rec.frequency for rec in stopped}
    }
    # A charge sits at most `before` days ahead of its occurrence or `after`
    # days behind it. The walk starts one reach before the month's own reach,
    # so the occurrences just before the month take their charges first.
    widest = max(before + after for before, after in reach.values())
    walk_start = month_start - timedelta(days=widest)
    linked = (await session.execute(
        select(Transaction.recurring_transaction_id, Transaction.date).where(
            Transaction.recurring_transaction_id.in_([rec.id for rec in stopped]),
            Transaction.date >= walk_start - timedelta(days=widest),
            Transaction.date < month_end + timedelta(days=widest),
        )
    )).all()
    charges: dict[uuid.UUID, list[date]] = {}
    for rec_id, charge_date in linked:
        charges.setdefault(rec_id, []).append(charge_date)

    charged: dict[uuid.UUID, set[date]] = {}
    for rec in stopped:
        pending = sorted(charges.get(rec.id, []))
        if not pending:
            continue
        before, after = reach[rec.frequency]
        for occurrence in get_occurrences_in_range(
            start=rec.start_date,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=walk_start,
            range_end=month_end,
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        ):
            in_reach = [
                day for day in pending
                if occurrence - timedelta(days=before) <= day <= occurrence + timedelta(days=after)
            ]
            if not in_reach:
                continue
            taken = min(in_reach, key=lambda day: (abs((day - occurrence).days), day))
            pending.remove(taken)
            if occurrence >= month_start:
                charged.setdefault(rec.id, set()).add(occurrence)
    return charged


async def get_month_summary(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    month: date,
    primary_currency: str,
) -> RecurringMonthSummary:
    """Income, expenses and net the definitions expect in ``month``.

    A stopped definition, paused or completed by generation, counts only the
    occurrences it charged in the month, so a completed one keeps its last
    month and a paused one adds nothing it never charged.

    Each definition counts once per occurrence in the month, from its own
    schedule (start date, frequency, day, end date, weekend adjustment), so a
    weekly one counts four or five times and a yearly one only in its month.
    Occurrences already generated count too: this is the month's plan, not
    what is still to come. Ignored categories are left out, and so are
    transfer-like ones, which are only counted. An amount in another
    currency converts from its native amount at the month's first-day rate,
    not from the stamped primary amount, which was taken in the currency of
    whoever created the definition; without a rate, it is left out and
    counted, never read 1:1.
    """
    month_start = month.replace(day=1)
    month_end = _advance_months(month_start, 1, 1)
    # Neither is_active nor the pointer tells a paused definition from one
    # generation completed: an edit can move the pointer of a paused one past
    # its end date. What a stopped definition really charged, through the
    # occurrences linked to it, is what counts for it.
    charged = await _charged_occurrences(session, workspace_id, month_start, month_end)
    result = await session.execute(
        select(RecurringTransaction)
        .outerjoin(Account, RecurringTransaction.account_id == Account.id)
        .where(
            RecurringTransaction.workspace_id == workspace_id,
            or_(
                RecurringTransaction.is_active == True,
                RecurringTransaction.id.in_(list(charged)),
            ),
            RecurringTransaction.start_date < month_end + timedelta(days=2),
            or_(Account.id.is_(None), Account.is_closed == False),
        )
        .options(selectinload(RecurringTransaction.category))
    )

    income = expenses = Decimal("0")
    transfer_count = unconverted_count = 0
    rates: dict[str, Optional[Decimal]] = {}
    for rec in result.scalars().all():
        occurrences = get_occurrences_in_range(
            start=rec.start_date,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=month_start,
            range_end=month_end,
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        )
        if not rec.is_active:
            occurrences = [day for day in occurrences if day in charged[rec.id]]
        if not occurrences:
            continue
        template = abs(rec.amount)
        if rec.has_category_lines and rec.category_lines:
            parts = [(line.category, abs(line.amount)) for line in rec.category_lines]
        else:
            parts = [(rec.category, template)]
        counted = sum(
            (amount for category, amount in parts
             if not (category and (category.is_ignored or category.treat_as_transfer))),
            Decimal("0"),
        )
        if any(category and category.treat_as_transfer
               and not category.is_ignored for category, _ in parts):
            transfer_count += len(occurrences)
        if not counted or not template:
            continue

        currency = (rec.currency or "").upper()
        if currency == primary_currency.upper():
            primary = template
        else:
            if currency not in rates:
                rates[currency] = (await convert_or_none(
                    session, Decimal("1"), currency, primary_currency.upper(),
                    month_start, decimals=10,
                ))[0]
            rate = rates[currency]
            if rate is None:
                unconverted_count += len(occurrences)
                continue
            primary = template * rate
        total = (primary * counted / template * len(occurrences)).quantize(Decimal("0.01"))
        if rec.type == "credit":
            income += total
        else:
            expenses += total

    return RecurringMonthSummary(
        month=month_start,
        currency=primary_currency,
        income=income,
        expenses=expenses,
        net=income - expenses,
        transfer_count=transfer_count,
        unconverted_count=unconverted_count,
    )


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
    # Unattended runs only touch schedules that opted in to auto-generation.
    generation_filter = (
        true() if include_manual else RecurringTransaction.auto_generate == True
    )
    if workspace_id is not None:
        generation_filter = and_(
            generation_filter, RecurringTransaction.workspace_id == workspace_id
        )
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
            or_(
                and_(
                    RecurringTransaction.weekend_adjustment == "previous_friday",
                    RecurringTransaction.next_occurrence <= latest_cutoff + timedelta(days=2),
                ),
                and_(
                    RecurringTransaction.weekend_adjustment != "previous_friday",
                    RecurringTransaction.next_occurrence <= latest_cutoff,
                ),
            ),
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
        # Generate while the effective date is due. The nominal pointer remains
        # authoritative and is the only date used for schedule advancement and
        # end-date evaluation.
        while True:
            effective_occurrence = adjust_weekend_date(
                recurring.next_occurrence, recurring.weekend_adjustment
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
                # A split bill hands its parts to an uncategorized charge.
                await category_line_service.apply_template_split(session, existing_real, recurring)
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
                    workspace_id=recurring.workspace_id,
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
                if recurring.has_category_lines:
                    # The occurrence starts split like its template.
                    category_line_service.copy_lines_to(transaction, recurring.category_lines)
                session.add(transaction)
                await session.flush()
                await stamp_primary_amount(session, user_id, transaction)
                # A new row reads the rules like any other (#568). The
                # template's category is already set, so category rules
                # leave it alone.
                await apply_rules_to_transaction(session, user_id, transaction)
                if transaction.status == "pending":
                    # The matcher skips ignored placeholders, so an ignored
                    # one would leave its bank charge to land beside it.
                    # Sync and import carry the rule's ignore over when the
                    # charge is folded in.
                    transaction.is_ignored = False
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
