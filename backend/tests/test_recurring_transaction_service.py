import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services.recurring_transaction_service import (
    _advance_date,
    adjust_weekend_date,
    create_recurring_transaction,
    delete_recurring_transaction,
    generate_pending,
    get_occurrences_in_range,
    get_recurring_transaction,
    get_recurring_transactions,
    update_recurring_transaction,
)


@pytest_asyncio.fixture
async def test_account_for_recurring(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="RecurAcc",
        type="checking",
        balance=Decimal("10000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_recurring_transaction(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    data = RecurringTransactionCreate(
        description="Netflix",
        amount=Decimal("39.90"),
        type="debit",
        frequency="monthly",
        start_date=date(2025, 1, 15),
        account_id=test_account_for_recurring.id,
    )
    rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)

    assert rec.id is not None
    assert rec.description == "Netflix"
    assert rec.amount == Decimal("39.90")
    assert rec.frequency == "monthly"
    assert rec.next_occurrence == date(2025, 1, 15)
    assert rec.is_active is True


@pytest.mark.asyncio
async def test_create_with_skip_first(session: AsyncSession, test_user, test_workspace, test_account_for_recurring):
    data = RecurringTransactionCreate(
        description="Rent",
        amount=Decimal("2000"),
        type="debit",
        frequency="monthly",
        start_date=date(2025, 3, 1),
        account_id=test_account_for_recurring.id,
        skip_first=True,
    )
    rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)

    # skip_first should advance to next month
    assert rec.start_date == date(2025, 3, 1)
    assert rec.next_occurrence == date(2025, 4, 1)


@pytest.mark.asyncio
async def test_get_recurring_transactions(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    for desc, dt in [("Sub1", date(2025, 1, 1)), ("Sub2", date(2025, 2, 1))]:
        await create_recurring_transaction(
            session,
            test_workspace.id, test_user.id,
            RecurringTransactionCreate(
                description=desc,
                amount=Decimal("10"),
                type="debit",
                frequency="monthly",
                start_date=dt,
                account_id=test_account_for_recurring.id,
            ),
        )

    result = await get_recurring_transactions(session, test_workspace.id)
    assert len(result) >= 2

    # Ordered by next_occurrence
    dates = [r.next_occurrence for r in result]
    assert dates == sorted(dates)


@pytest.mark.asyncio
async def test_get_recurring_transaction_by_id(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    created = await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Lookup",
            amount=Decimal("50"),
            type="debit",
            frequency="weekly",
            start_date=date(2025, 6, 1),
            account_id=test_account_for_recurring.id,
        ),
    )
    fetched = await get_recurring_transaction(session, created.id, test_workspace.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_recurring_transaction_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_recurring_transaction(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_recurring_transaction(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Old",
            amount=Decimal("100"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            account_id=test_account_for_recurring.id,
        ),
    )
    updated = await update_recurring_transaction(
        session,
        rec.id,
        test_workspace.id,
        RecurringTransactionUpdate(description="Updated", amount=Decimal("150")),
    )
    assert updated is not None
    assert updated.description == "Updated"
    assert updated.amount == Decimal("150")


@pytest.mark.asyncio
async def test_update_recurring_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_recurring_transaction(
        session,
        uuid.uuid4(),
        test_workspace.id,
        RecurringTransactionUpdate(description="Nope"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_recurring_transaction(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="ToDelete",
            amount=Decimal("10"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            account_id=test_account_for_recurring.id,
        ),
    )
    assert await delete_recurring_transaction(session, rec.id, test_workspace.id) is True
    assert await get_recurring_transaction(session, rec.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_recurring_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_recurring_transaction(session, uuid.uuid4(), test_workspace.id) is False


# ---------------------------------------------------------------------------
# _advance_date
# ---------------------------------------------------------------------------


def test_advance_date_monthly():
    assert _advance_date(date(2025, 1, 15), "monthly") == date(2025, 2, 15)
    assert _advance_date(date(2025, 12, 10), "monthly") == date(2026, 1, 10)


def test_advance_date_monthly_overflow():
    # Jan 31 -> Feb should clamp to 28
    assert _advance_date(date(2025, 1, 31), "monthly") == date(2025, 2, 28)
    # Leap year: Jan 31 -> Feb 29
    assert _advance_date(date(2024, 1, 31), "monthly") == date(2024, 2, 29)


def test_advance_date_weekly():
    assert _advance_date(date(2025, 1, 1), "weekly") == date(2025, 1, 8)
    assert _advance_date(date(2025, 12, 29), "weekly") == date(2026, 1, 5)


def test_advance_date_biweekly():
    assert _advance_date(date(2026, 1, 5), "biweekly") == date(2026, 1, 19)
    assert _advance_date(date(2026, 12, 28), "biweekly") == date(2027, 1, 11)


def test_advance_date_yearly():
    assert _advance_date(date(2025, 3, 15), "yearly") == date(2026, 3, 15)
    # Leap year: Feb 29 -> Feb 28 next year
    assert _advance_date(date(2024, 2, 29), "yearly") == date(2025, 2, 28)


def test_advance_date_monthly_intended_day_recovers_after_february():
    # After Jan 31 clamps to Feb 28, advancing again must hit Mar 31 — not drift to Mar 28.
    feb = _advance_date(date(2026, 1, 31), "monthly", intended_day=31)
    assert feb == date(2026, 2, 28)
    mar = _advance_date(feb, "monthly", intended_day=31)
    assert mar == date(2026, 3, 31)
    apr = _advance_date(mar, "monthly", intended_day=31)
    assert apr == date(2026, 4, 30)  # April has 30 days
    may = _advance_date(apr, "monthly", intended_day=31)
    assert may == date(2026, 5, 31)


def test_advance_date_monthly_intended_day_30():
    # Day 30 pattern: Jan 30 -> Feb 28 -> Mar 30 (not Mar 28).
    feb = _advance_date(date(2026, 1, 30), "monthly", intended_day=30)
    assert feb == date(2026, 2, 28)
    mar = _advance_date(feb, "monthly", intended_day=30)
    assert mar == date(2026, 3, 30)


def test_advance_date_yearly_intended_day_leap_recovery():
    # Feb 29 on a leap year should recover to Feb 29 four years later, not stick at 28.
    y1 = _advance_date(date(2024, 2, 29), "yearly", intended_day=29)
    assert y1 == date(2025, 2, 28)
    y2 = _advance_date(y1, "yearly", intended_day=29)
    assert y2 == date(2026, 2, 28)
    y3 = _advance_date(y2, "yearly", intended_day=29)
    assert y3 == date(2027, 2, 28)
    y4 = _advance_date(y3, "yearly", intended_day=29)
    assert y4 == date(2028, 2, 29)


def test_advance_date_quarterly():
    assert _advance_date(date(2026, 1, 15), "quarterly") == date(2026, 4, 15)
    assert _advance_date(date(2026, 11, 15), "quarterly") == date(2027, 2, 15)


def test_advance_date_quarterly_intended_day_recovers_after_clamping():
    april = _advance_date(date(2026, 1, 31), "quarterly", intended_day=31)
    assert april == date(2026, 4, 30)
    july = _advance_date(april, "quarterly", intended_day=31)
    assert july == date(2026, 7, 31)
    october = _advance_date(july, "quarterly", intended_day=31)
    assert october == date(2026, 10, 31)
    january = _advance_date(october, "quarterly", intended_day=31)
    assert january == date(2027, 1, 31)


def test_advance_date_quarterly_leap_year_clamping_recovers():
    february = _advance_date(date(2023, 11, 30), "quarterly", intended_day=30)
    assert february == date(2024, 2, 29)
    may = _advance_date(february, "quarterly", intended_day=30)
    assert may == date(2024, 5, 30)


def test_advance_date_semiannual_preserves_intended_day():
    july = _advance_date(date(2026, 1, 31), "semiannual", intended_day=31)
    assert july == date(2026, 7, 31)
    january = _advance_date(july, "semiannual", intended_day=31)
    assert january == date(2027, 1, 31)

    february = _advance_date(date(2024, 8, 31), "semiannual", intended_day=31)
    assert february == date(2025, 2, 28)
    august = _advance_date(february, "semiannual", intended_day=31)
    assert august == date(2025, 8, 31)


@pytest.mark.parametrize(
    ("nominal", "policy", "expected"),
    [
        (date(2026, 8, 1), "none", date(2026, 8, 1)),
        (date(2026, 8, 1), "previous_friday", date(2026, 7, 31)),
        (date(2026, 8, 1), "next_monday", date(2026, 8, 3)),
        (date(2026, 8, 2), "none", date(2026, 8, 2)),
        (date(2026, 8, 2), "previous_friday", date(2026, 7, 31)),
        (date(2026, 8, 2), "next_monday", date(2026, 8, 3)),
        (date(2026, 8, 3), "none", date(2026, 8, 3)),
        (date(2026, 8, 3), "previous_friday", date(2026, 8, 3)),
        (date(2026, 8, 3), "next_monday", date(2026, 8, 3)),
        (date(2026, 8, 7), "none", date(2026, 8, 7)),
        (date(2026, 8, 7), "previous_friday", date(2026, 8, 7)),
        (date(2026, 8, 7), "next_monday", date(2026, 8, 7)),
    ],
)
def test_adjust_weekend_date(nominal, policy, expected):
    assert adjust_weekend_date(nominal, policy) == expected


def test_adjust_weekend_date_rejects_unsupported_policy_on_weekday():
    with pytest.raises(
        ValueError, match="Unsupported weekend adjustment: nearest_weekday"
    ):
        adjust_weekend_date(date(2026, 8, 3), "nearest_weekday")


@pytest.mark.parametrize(
    ("frequency", "start", "intended_day", "expected"),
    [
        (
            "weekly",
            date(2026, 8, 1),
            1,
            [
                (date(2026, 8, 1), date(2026, 7, 31)),
                (date(2026, 8, 8), date(2026, 8, 7)),
                (date(2026, 8, 15), date(2026, 8, 14)),
            ],
        ),
        (
            "monthly",
            date(2026, 1, 31),
            31,
            [
                (date(2026, 1, 31), date(2026, 1, 30)),
                (date(2026, 2, 28), date(2026, 2, 27)),
                (date(2026, 3, 31), date(2026, 3, 31)),
            ],
        ),
        (
            "quarterly",
            date(2026, 1, 31),
            31,
            [
                (date(2026, 1, 31), date(2026, 1, 30)),
                (date(2026, 4, 30), date(2026, 4, 30)),
                (date(2026, 7, 31), date(2026, 7, 31)),
            ],
        ),
        (
            "yearly",
            date(2024, 2, 29),
            29,
            [
                (date(2024, 2, 29), date(2024, 2, 29)),
                (date(2025, 2, 28), date(2025, 2, 28)),
                (date(2026, 2, 28), date(2026, 2, 27)),
            ],
        ),
        (
            "biweekly",
            date(2026, 8, 1),
            1,
            [
                (date(2026, 8, 1), date(2026, 7, 31)),
                (date(2026, 8, 15), date(2026, 8, 14)),
                (date(2026, 8, 29), date(2026, 8, 28)),
            ],
        ),
        (
            "semiannual",
            date(2024, 8, 31),
            31,
            [
                (date(2024, 8, 31), date(2024, 8, 30)),
                (date(2025, 2, 28), date(2025, 2, 28)),
                (date(2025, 8, 31), date(2025, 8, 29)),
            ],
        ),
    ],
)
def test_weekend_adjustment_never_changes_nominal_cadence(
    frequency, start, intended_day, expected
):
    current = start
    actual = []
    for _ in expected:
        actual.append((current, adjust_weekend_date(current, "previous_friday")))
        current = _advance_date(current, frequency, intended_day=intended_day)
    assert actual == expected


def test_occurrence_range_uses_effective_date_across_month_boundary():
    july = get_occurrences_in_range(
        start=date(2026, 8, 1),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 7, 1),
        range_end=date(2026, 8, 1),
        weekend_adjustment="previous_friday",
    )
    august = get_occurrences_in_range(
        start=date(2026, 8, 1),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 8, 1),
        range_end=date(2026, 9, 1),
        weekend_adjustment="previous_friday",
    )

    assert july == [date(2026, 7, 31)]
    assert august == []
    assert july.count(date(2026, 7, 31)) + august.count(date(2026, 7, 31)) == 1


def test_occurrence_range_moves_sunday_to_following_monday():
    assert get_occurrences_in_range(
        start=date(2026, 8, 2),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 8, 1),
        range_end=date(2026, 9, 1),
        weekend_adjustment="next_monday",
    ) == [date(2026, 8, 3)]


# ---------------------------------------------------------------------------
# get_occurrences_in_range
# ---------------------------------------------------------------------------


def test_get_occurrences_in_range_monthly():
    occurrences = get_occurrences_in_range(
        start=date(2025, 1, 1),
        frequency="monthly",
        end_date=None,
        range_start=date(2025, 3, 1),
        range_end=date(2025, 6, 1),
    )
    assert occurrences == [date(2025, 3, 1), date(2025, 4, 1), date(2025, 5, 1)]


def test_get_occurrences_in_range_respects_end_date():
    occurrences = get_occurrences_in_range(
        start=date(2025, 1, 1),
        frequency="monthly",
        end_date=date(2025, 4, 15),
        range_start=date(2025, 3, 1),
        range_end=date(2025, 12, 1),
    )
    assert occurrences == [date(2025, 3, 1), date(2025, 4, 1)]


def test_get_occurrences_in_range_weekly():
    occurrences = get_occurrences_in_range(
        start=date(2025, 1, 6),
        frequency="weekly",
        end_date=None,
        range_start=date(2025, 1, 6),
        range_end=date(2025, 1, 27),
    )
    assert len(occurrences) == 3  # Jan 6, 13, 20 (range_end is exclusive)


def test_get_occurrences_in_range_empty():
    occurrences = get_occurrences_in_range(
        start=date(2025, 6, 1),
        frequency="monthly",
        end_date=None,
        range_start=date(2025, 1, 1),
        range_end=date(2025, 3, 1),
    )
    assert occurrences == []


def test_get_occurrences_in_range_monthly_day_31_does_not_drift():
    # Regression test for #67: after Feb clamps to 28, subsequent months must
    # recover to their true month-end (31 / 30 / 31 / 30) instead of sticking at 28.
    occurrences = get_occurrences_in_range(
        start=date(2026, 1, 31),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 7, 1),
        intended_day=31,
    )
    assert occurrences == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_get_occurrences_in_range_monthly_day_30_does_not_drift():
    occurrences = get_occurrences_in_range(
        start=date(2026, 1, 30),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 7, 1),
        intended_day=30,
    )
    assert occurrences == [
        date(2026, 1, 30),
        date(2026, 2, 28),
        date(2026, 3, 30),
        date(2026, 4, 30),
        date(2026, 5, 30),
        date(2026, 6, 30),
    ]


def test_get_occurrences_in_range_monthly_day_29_does_not_drift():
    occurrences = get_occurrences_in_range(
        start=date(2026, 1, 29),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 1, 1),
        range_end=date(2026, 7, 1),
        intended_day=29,
    )
    assert occurrences == [
        date(2026, 1, 29),
        date(2026, 2, 28),
        date(2026, 3, 29),
        date(2026, 4, 29),
        date(2026, 5, 29),
        date(2026, 6, 29),
    ]


def test_get_occurrences_in_range_quarterly():
    occurrences = get_occurrences_in_range(
        start=date(2026, 1, 31),
        frequency="quarterly",
        end_date=None,
        range_start=date(2026, 1, 1),
        range_end=date(2027, 2, 1),
        intended_day=31,
    )
    assert occurrences == [
        date(2026, 1, 31),
        date(2026, 4, 30),
        date(2026, 7, 31),
        date(2026, 10, 31),
        date(2027, 1, 31),
    ]


def test_get_occurrences_in_range_quarterly_respects_end_date():
    occurrences = get_occurrences_in_range(
        start=date(2026, 1, 15),
        frequency="quarterly",
        end_date=date(2026, 7, 1),
        range_start=date(2026, 1, 1),
        range_end=date(2027, 1, 1),
    )
    assert occurrences == [date(2026, 1, 15), date(2026, 4, 15)]


@pytest.mark.parametrize(
    ("frequency", "start", "range_start", "range_end", "intended_day", "expected"),
    [
        (
            "biweekly",
            date(2026, 1, 5),
            date(2026, 1, 1),
            date(2026, 2, 10),
            None,
            [date(2026, 1, 5), date(2026, 1, 19), date(2026, 2, 2)],
        ),
        (
            "semiannual",
            date(2024, 8, 31),
            date(2024, 8, 1),
            date(2025, 9, 1),
            31,
            [date(2024, 8, 31), date(2025, 2, 28), date(2025, 8, 31)],
        ),
    ],
    ids=["biweekly", "semiannual"],
)
def test_get_occurrences_in_range_new_frequencies(
    frequency, start, range_start, range_end, intended_day, expected
):
    assert get_occurrences_in_range(
        start=start,
        frequency=frequency,
        end_date=None,
        range_start=range_start,
        range_end=range_end,
        intended_day=intended_day,
    ) == expected


# ---------------------------------------------------------------------------
# generate_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pending(session: AsyncSession, test_user, test_workspace, test_account_for_recurring):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Monthly Sub",
            amount=Decimal("29.90"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            account_id=test_account_for_recurring.id,
        ),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 3, 15))
    assert count == 3  # Jan, Feb, Mar

    # Verify transactions were created
    result = await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "recurring",
            Transaction.description == "Monthly Sub",
        )
    )
    txns = result.scalars().all()
    assert len(txns) == 3
    # Only occurrences that already came due are materialized, so they are
    # actuals. Marking them pending would keep them out of every current
    # figure with no way back on an account that never syncs.
    assert {tx.status for tx in txns} == {"posted"}

    # next_occurrence should be advanced past cutoff
    await session.refresh(rec)
    assert rec.next_occurrence == date(2025, 4, 1)


@pytest.mark.asyncio
async def test_generate_pending_holds_synced_account_rows_pending(
    session: AsyncSession, test_user, test_workspace, test_connection
):
    """On a synced account the placeholder waits for the real charge.

    The incoming charge often fails to match, and two posted rows for one
    charge inflate the balance silently. Holding it pending makes a missed
    match cost a visible stale row instead of a wrong number.
    """
    synced = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
        connection_id=test_connection.id,
    )
    session.add(synced)
    await session.commit()

    await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Synced Sub",
            amount=Decimal("10"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            account_id=synced.id,
        ),
    )
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 1)) == 1

    result = await session.execute(
        select(Transaction).where(Transaction.account_id == synced.id)
    )
    [txn] = result.scalars().all()
    assert txn.status == "pending"


@pytest.mark.asyncio
async def test_generate_pending_quarterly_respects_end_date(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Quarterly Insurance",
            amount=Decimal("300"),
            type="debit",
            frequency="quarterly",
            day_of_month=30,
            start_date=date(2023, 11, 30),
            end_date=date(2024, 5, 30),
            account_id=test_account_for_recurring.id,
        ),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2024, 12, 31))
    assert count == 3

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.recurring_transaction_id == rec.id,
            Transaction.source == "recurring",
        )
        .order_by(Transaction.date)
    )
    assert [transaction.date for transaction in result.scalars()] == [
        date(2023, 11, 30),
        date(2024, 2, 29),
        date(2024, 5, 30),
    ]

    await session.refresh(rec)
    assert rec.next_occurrence == date(2024, 8, 30)
    assert rec.is_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "frequency",
        "start",
        "up_to",
        "day_of_month",
        "expected_dates",
        "expected_next",
    ),
    [
        (
            "biweekly",
            date(2026, 1, 5),
            date(2026, 1, 19),
            None,
            [date(2026, 1, 5), date(2026, 1, 19)],
            date(2026, 2, 2),
        ),
        (
            "semiannual",
            date(2024, 8, 31),
            date(2025, 2, 28),
            31,
            [date(2024, 8, 31), date(2025, 2, 28)],
            date(2025, 8, 31),
        ),
    ],
    ids=["biweekly", "semiannual"],
)
async def test_generate_pending_new_frequencies(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account_for_recurring,
    frequency,
    start,
    up_to,
    day_of_month,
    expected_dates,
    expected_next,
):
    recurring = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description=f"{frequency} regression",
            amount=Decimal("25"),
            type="debit",
            frequency=frequency,
            day_of_month=day_of_month,
            start_date=start,
            account_id=test_account_for_recurring.id,
        ),
    )

    await generate_pending(session, test_user.id, up_to=up_to)

    result = await session.execute(
        select(Transaction)
        .where(Transaction.recurring_transaction_id == recurring.id)
        .order_by(Transaction.date)
    )
    actual_dates = [transaction.date for transaction in result.scalars()]
    await session.refresh(recurring)
    assert (actual_dates, recurring.next_occurrence) == (expected_dates, expected_next)


@pytest.mark.asyncio
async def test_generate_pending_deactivates_past_end_date(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Short Sub",
            amount=Decimal("10"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 15),
            account_id=test_account_for_recurring.id,
        ),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 12, 31))
    # Should create Jan and Feb (Feb 1 <= Feb 15), then deactivate
    assert count == 2

    await session.refresh(rec)
    assert rec.is_active is False


@pytest.mark.asyncio
async def test_generate_pending_no_duplicates(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    await create_recurring_transaction(
        session,
        test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="NoDup",
            amount=Decimal("5"),
            type="debit",
            frequency="monthly",
            start_date=date(2025, 1, 1),
            account_id=test_account_for_recurring.id,
        ),
    )

    # Generate once
    count1 = await generate_pending(session, test_user.id, up_to=date(2025, 3, 1))
    # Generate again with same cutoff — should produce 0
    count2 = await generate_pending(session, test_user.id, up_to=date(2025, 3, 1))
    assert count1 == 3
    assert count2 == 0


@pytest.mark.asyncio
async def test_generate_pending_previous_friday_uses_effective_cutoff_and_date(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Weekend rent",
            amount=Decimal("1000"),
            type="debit",
            frequency="monthly",
            start_date=date(2026, 8, 1),
            weekend_adjustment="previous_friday",
            account_id=test_account_for_recurring.id,
        ),
    )

    assert await generate_pending(session, test_user.id, up_to=date(2026, 7, 31)) == 1
    transaction = (
        await session.execute(
            select(Transaction).where(Transaction.recurring_transaction_id == rec.id)
        )
    ).scalar_one()
    assert transaction.date == date(2026, 7, 31)
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_generate_pending_next_monday_waits_until_effective_date(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Weekend salary",
            amount=Decimal("2000"),
            type="credit",
            frequency="monthly",
            start_date=date(2026, 8, 2),
            weekend_adjustment="next_monday",
            account_id=test_account_for_recurring.id,
        ),
    )

    assert await generate_pending(session, test_user.id, up_to=date(2026, 8, 2)) == 0
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 8, 2)

    assert await generate_pending(session, test_user.id, up_to=date(2026, 8, 3)) == 1
    transaction = (
        await session.execute(
            select(Transaction).where(Transaction.recurring_transaction_id == rec.id)
        )
    ).scalar_one()
    assert transaction.date == date(2026, 8, 3)
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 9, 2)


@pytest.mark.asyncio
async def test_generate_pending_weekend_adjustment_respects_nominal_end_date(
    session: AsyncSession, test_user, test_workspace, test_account_for_recurring
):
    rec = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Final weekend bill",
            amount=Decimal("50"),
            type="debit",
            frequency="monthly",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 1),
            weekend_adjustment="previous_friday",
            account_id=test_account_for_recurring.id,
        ),
    )

    assert await generate_pending(session, test_user.id, up_to=date(2026, 7, 31)) == 1
    assert await generate_pending(session, test_user.id, up_to=date(2026, 12, 31)) == 0
    result = await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == rec.id)
    )
    assert [transaction.date for transaction in result.scalars()] == [date(2026, 7, 31)]
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 9, 1)
    assert rec.is_active is False
