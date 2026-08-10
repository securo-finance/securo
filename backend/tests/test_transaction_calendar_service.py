import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.transaction_calendar_service import get_transaction_calendar


@pytest.mark.asyncio
async def test_transaction_calendar_combines_actual_projected_and_balances(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Person",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Bills",
        icon="receipt",
        color="#f97316",
    )
    session.add_all([account, category])
    await session.flush()

    salary = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        category_id=category.id,
        description="Salary",
        amount=Decimal("1000"),
        currency="BRL",
        date=date(2026, 7, 2),
        type="credit",
        source="manual",
    )
    groceries = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        category_id=category.id,
        description="Groceries",
        amount=Decimal("200"),
        currency="BRL",
        date=date(2026, 7, 5),
        type="debit",
        source="manual",
    )
    rent = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        category_id=category.id,
        description="Rent",
        amount=Decimal("300"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 7, 10),
        next_occurrence=date(2026, 7, 10),
    )
    session.add_all([salary, groceries, rent])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )

    assert calendar.month == "2026-07"
    assert calendar.currency == "BRL"
    assert calendar.days[0].date == date(2026, 6, 28)
    assert calendar.days[-1].date == date(2026, 8, 1)

    july_2 = next(day for day in calendar.days if day.date == date(2026, 7, 2))
    assert july_2.income == 1000.0
    assert july_2.actual_income == 1000.0
    assert july_2.projected_income == 0.0
    assert july_2.ending_balance == 1000.0
    assert july_2.actual_count == 1
    assert july_2.items[0].kind == "actual"

    july_5 = next(day for day in calendar.days if day.date == date(2026, 7, 5))
    assert july_5.expense == 200.0
    assert july_5.actual_expense == 200.0
    assert july_5.projected_expense == 0.0
    assert july_5.ending_balance == 800.0

    july_10 = next(day for day in calendar.days if day.date == date(2026, 7, 10))
    assert july_10.projected_count == 1
    assert july_10.expense == 300.0
    assert july_10.actual_expense == 0.0
    assert july_10.projected_expense == 300.0
    assert july_10.ending_balance == 500.0
    assert july_10.items[0].kind == "projected"
    assert july_10.items[0].recurring_id == rent.id


@pytest.mark.asyncio
async def test_transaction_calendar_respects_account_filter(
    session: AsyncSession, test_user, test_workspace
):
    kept = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Kept", type="checking", balance=Decimal("0"), currency="BRL",
    )
    other = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Other", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add_all([kept, other])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=kept.id, description="Kept income", amount=Decimal("100"),
            currency="BRL", date=date(2026, 7, 3), type="credit", source="manual",
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=other.id, description="Other income", amount=Decimal("999"),
            currency="BRL", date=date(2026, 7, 3), type="credit", source="manual",
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session,
        test_workspace.id,
        test_user.id,
        month=date(2026, 7, 1),
        account_ids=[kept.id],
    )

    july_3 = next(day for day in calendar.days if day.date == date(2026, 7, 3))
    assert july_3.income == 100.0
    assert july_3.actual_income == 100.0
    assert july_3.ending_balance == 100.0
    assert [item.description for item in july_3.items] == ["Kept income"]


@pytest.mark.asyncio
async def test_transaction_calendar_single_foreign_account_uses_primary_currency_balance(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="USD checking",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add_all([
        account,
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date.today(),
            rate=Decimal("5"),
            source="test",
        ),
    ])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Starting cash", amount=Decimal("100"),
            currency="USD", date=date(2026, 6, 20), type="credit", source="opening_balance",
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Coffee", amount=Decimal("10"),
            currency="USD", date=date(2026, 7, 2), type="debit", source="manual",
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session,
        test_workspace.id,
        test_user.id,
        month=date(2026, 7, 1),
        account_ids=[account.id],
    )

    assert calendar.currency == "BRL"
    july_1 = next(day for day in calendar.days if day.date == date(2026, 7, 1))
    assert july_1.ending_balance == 500.0
    july_2 = next(day for day in calendar.days if day.date == date(2026, 7, 2))
    assert july_2.expense == 50.0
    assert july_2.actual_expense == 50.0
    assert july_2.projected_expense == 0.0
    assert july_2.ending_balance == 450.0


@pytest.mark.asyncio
async def test_transaction_calendar_skips_closed_account_recurring_projections(
    session: AsyncSession, test_user, test_workspace
):
    closed_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Closed",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
        is_closed=True,
    )
    session.add(closed_account)
    await session.flush()
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=closed_account.id,
            description="Old subscription",
            amount=Decimal("25"),
            currency="BRL",
            type="debit",
            frequency="monthly",
            start_date=date(2026, 7, 10),
            next_occurrence=date(2026, 7, 10),
        )
    )
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )

    july_10 = next(day for day in calendar.days if day.date == date(2026, 7, 10))
    assert july_10.projected_count == 0
    assert july_10.items == []
    assert july_10.ending_balance == 0.0


@pytest.mark.asyncio
async def test_transaction_calendar_splits_mixed_day_and_keeps_combined_totals(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Person", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Freelance", amount=Decimal("400"),
            currency="BRL", date=date(2026, 7, 15), type="credit", source="manual",
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Market", amount=Decimal("120"),
            currency="BRL", date=date(2026, 7, 15), type="debit", source="manual",
        ),
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Streaming", amount=Decimal("50"),
            currency="BRL", type="debit", frequency="monthly",
            start_date=date(2026, 7, 15), next_occurrence=date(2026, 7, 15),
        ),
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Dividends", amount=Decimal("80"),
            currency="BRL", type="credit", frequency="monthly",
            start_date=date(2026, 7, 15), next_occurrence=date(2026, 7, 15),
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )

    july_15 = next(day for day in calendar.days if day.date == date(2026, 7, 15))
    assert july_15.actual_income == 400.0
    assert july_15.actual_expense == 120.0
    assert july_15.projected_income == 80.0
    assert july_15.projected_expense == 50.0
    # Combined totals stay the sum of both buckets for backwards compatibility.
    assert july_15.income == 480.0
    assert july_15.expense == 170.0
    assert july_15.ending_balance == 310.0


@pytest.mark.asyncio
async def test_transaction_calendar_transfer_buckets_stay_out_of_activity(
    session: AsyncSession, test_user, test_workspace
):
    checking = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Checking", type="checking", balance=Decimal("0"), currency="BRL",
    )
    savings = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Savings", type="savings", balance=Decimal("0"), currency="BRL",
    )
    transfer_category = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Transfers", icon="repeat", color="#0ea5e9", treat_as_transfer=True,
    )
    session.add_all([checking, savings, transfer_category])
    await session.flush()

    pair_id = uuid.uuid4()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=checking.id, description="To savings", amount=Decimal("250"),
            currency="BRL", date=date(2026, 7, 8), type="debit", source="manual",
            transfer_pair_id=pair_id,
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=savings.id, description="From checking", amount=Decimal("250"),
            currency="BRL", date=date(2026, 7, 8), type="credit", source="manual",
            transfer_pair_id=pair_id,
        ),
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=checking.id, category_id=transfer_category.id,
            description="Auto savings", amount=Decimal("100"), currency="BRL",
            type="debit", frequency="monthly",
            start_date=date(2026, 7, 20), next_occurrence=date(2026, 7, 20),
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )

    july_8 = next(day for day in calendar.days if day.date == date(2026, 7, 8))
    assert july_8.actual_income == 0.0
    assert july_8.actual_expense == 0.0
    assert july_8.income == 0.0
    assert july_8.expense == 0.0
    assert july_8.actual_transfer_net == 0.0
    assert july_8.has_transfer is True
    # Both legs stay listed even though they never count as income/expense.
    assert len(july_8.items) == 2

    july_20 = next(day for day in calendar.days if day.date == date(2026, 7, 20))
    assert july_20.projected_expense == 0.0
    assert july_20.projected_transfer_net == -100.0
    assert july_20.transfer_net == -100.0
    assert july_20.has_transfer is True
    # Projected transfers keep moving the future balance.
    assert july_20.ending_balance == -100.0


@pytest.mark.asyncio
async def test_transaction_calendar_opening_balance_and_ignored_category(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Person", type="checking", balance=Decimal("0"), currency="BRL",
    )
    ignored_category = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Reimbursed", icon="undo", color="#64748b", is_ignored=True,
    )
    session.add_all([account, ignored_category])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Opening", amount=Decimal("900"),
            currency="BRL", date=date(2026, 7, 1), type="credit", source="opening_balance",
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=ignored_category.id,
            description="Work lunch", amount=Decimal("60"), currency="BRL",
            date=date(2026, 7, 4), type="debit", source="manual",
        ),
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=ignored_category.id,
            description="Reimbursed sub", amount=Decimal("30"), currency="BRL",
            type="debit", frequency="monthly",
            start_date=date(2026, 7, 12), next_occurrence=date(2026, 7, 12),
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )

    july_1 = next(day for day in calendar.days if day.date == date(2026, 7, 1))
    assert july_1.income == 0.0
    assert july_1.actual_income == 0.0
    assert july_1.ending_balance == 900.0

    july_4 = next(day for day in calendar.days if day.date == date(2026, 7, 4))
    assert july_4.expense == 0.0
    assert july_4.actual_expense == 0.0
    assert july_4.actual_count == 1
    # Ignored actuals are excluded from the balance deltas too (existing behavior).
    assert july_4.ending_balance == 900.0

    july_12 = next(day for day in calendar.days if day.date == date(2026, 7, 12))
    assert july_12.expense == 0.0
    assert july_12.projected_expense == 0.0
    assert july_12.projected_count == 1
    assert july_12.items[0].is_ignored is True
    # Still listed, but ignored means ignored: the projection is kept out of the
    # balance too, matching how the posted version is treated on July 4.
    assert july_12.ending_balance == 900.0


@pytest.mark.asyncio
async def test_transaction_calendar_ignored_projection_matches_posted_balance(
    session: AsyncSession, test_user, test_workspace
):
    """A projected ignored occurrence must land on the same balance its posted
    sibling produces. Otherwise the calendar promises a future balance the app
    reverts the moment the recurring becomes a real transaction."""
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Checking", type="checking", balance=Decimal("0"), currency="BRL",
    )
    ignored_category = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Reimbursed", icon="undo", color="#64748b", is_ignored=True,
    )
    session.add_all([account, ignored_category])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="Seed", amount=Decimal("1000"),
            currency="BRL", date=date(2026, 7, 1), type="credit", source="manual",
        ),
        # The July 6 occurrence already posted as a real transaction.
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=ignored_category.id,
            description="Reimbursed sub", amount=Decimal("30"), currency="BRL",
            date=date(2026, 7, 6), type="debit", source="manual",
        ),
        # The later ones have not, so they are still projected in the same grid.
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=ignored_category.id,
            description="Reimbursed sub", amount=Decimal("30"), currency="BRL",
            type="debit", frequency="weekly",
            start_date=date(2026, 7, 6), next_occurrence=date(2026, 7, 13),
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )
    by_date = {day.date: day for day in calendar.days}

    posted = by_date[date(2026, 7, 6)]
    assert posted.items[0].kind == "actual"
    assert posted.actual_count == 1

    # Every row stays visible on its day, whether posted or projected.
    for occurrence in (date(2026, 7, 13), date(2026, 7, 20), date(2026, 7, 27)):
        assert by_date[occurrence].projected_count == 1
        assert by_date[occurrence].items[0].is_ignored is True

    # None of them moves the balance, so each projection is one the app can
    # honour. Before this, the balance drifted down 30 per projected occurrence
    # and snapped back as each one posted.
    for day in (date(2026, 7, 6), date(2026, 7, 13), date(2026, 7, 20), date(2026, 7, 27)):
        assert by_date[day].ending_balance == 1000.0


@pytest.mark.asyncio
async def test_transaction_calendar_uses_effective_weekend_date_and_balance(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Boundary account",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )

    recurring = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="August rent",
        amount=Decimal("300"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 8, 1),
        next_occurrence=date(2026, 8, 1),
        weekend_adjustment="previous_friday",
    )
    session.add_all([account, recurring])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 7, 1)
    )
    july_31 = next(day for day in calendar.days if day.date == date(2026, 7, 31))
    august_1 = next(day for day in calendar.days if day.date == date(2026, 8, 1))

    assert [item.description for item in july_31.items] == ["August rent"]
    assert july_31.projected_expense == 300.0
    assert july_31.ending_balance == -300.0
    assert august_1.projected_count == 0
    assert august_1.ending_balance == -300.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nominal_date", "weekend_adjustment", "effective_date", "comparison_date"),
    [
        (
            date(2026, 8, 30),
            "previous_friday",
            date(2026, 8, 28),
            date(2026, 8, 30),
        ),
        (
            date(2026, 8, 29),
            "next_monday",
            date(2026, 8, 31),
            date(2026, 8, 31),
        ),
    ],
)
async def test_transaction_calendar_weekend_balance_matches_overlapping_views(
    session: AsyncSession,
    test_user,
    test_workspace,
    nominal_date: date,
    weekend_adjustment: str,
    effective_date: date,
    comparison_date: date,
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    recurring = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Weekend-adjusted debit",
        amount=Decimal("100"),
        currency="BRL",
        type="debit",
        frequency="weekly",
        start_date=nominal_date,
        next_occurrence=nominal_date,
        weekend_adjustment=weekend_adjustment,
    )
    session.add_all([
        account,
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Opening",
            amount=Decimal("1000"),
            currency="BRL",
            date=date(2026, 7, 1),
            type="credit",
            source="opening_balance",
        ),
        recurring,
    ])
    await session.commit()

    august = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 8, 1)
    )
    september = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 9, 1)
    )
    august_by_date = {day.date: day for day in august.days}
    september_by_date = {day.date: day for day in september.days}

    assert august_by_date[effective_date].projected_count == 1
    if effective_date in september_by_date:
        assert september_by_date[effective_date].projected_count == 1
    assert august_by_date[comparison_date].ending_balance == 900.0
    assert september_by_date[comparison_date].ending_balance == 900.0


@pytest.mark.asyncio
async def test_transaction_calendar_overlap_carries_virtual_occurrences(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()

    recurring = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Weekly debit",
        amount=Decimal("100"),
        currency="BRL",
        type="debit",
        frequency="weekly",
        start_date=date(2026, 8, 3),
        next_occurrence=date(2026, 8, 3),
    )
    session.add_all([
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Opening",
            amount=Decimal("1000"),
            currency="BRL",
            date=date(2026, 7, 1),
            type="credit",
            source="opening_balance",
        ),
        recurring,
    ])
    await session.commit()

    august = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 8, 1)
    )
    september = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 9, 1)
    )
    august_by_date = {day.date: day for day in august.days}
    september_by_date = {day.date: day for day in september.days}

    august_september_1 = august_by_date[date(2026, 9, 1)]
    september_september_1 = september_by_date[date(2026, 9, 1)]
    assert august_september_1.ending_balance == 500.0
    assert september_september_1.ending_balance == 500.0
    assert august_september_1.ending_balance == september_september_1.ending_balance

    september_grid_start = september_by_date[date(2026, 8, 30)]
    assert september_grid_start.ending_balance == 600.0
    assert september_grid_start.projected_count == 0
    assert september_grid_start.income == 0.0
    assert september_grid_start.expense == 0.0
    assert september_grid_start.transfer_net == 0.0
    assert september_grid_start.items == []


@pytest.mark.asyncio
async def test_transaction_calendar_does_not_carry_ignored_virtual_occurrences(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    ignored_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        icon="eye-off",
        color="#64748b",
        is_ignored=True,
    )
    session.add_all([account, ignored_category])
    await session.flush()

    recurring = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        category_id=ignored_category.id,
        description="Ignored weekly debit",
        amount=Decimal("100"),
        currency="BRL",
        type="debit",
        frequency="weekly",
        start_date=date(2026, 8, 3),
        next_occurrence=date(2026, 8, 3),
    )
    session.add_all([
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Opening",
            amount=Decimal("1000"),
            currency="BRL",
            date=date(2026, 7, 1),
            type="credit",
            source="opening_balance",
        ),
        recurring,
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 9, 1)
    )
    by_date = {day.date: day for day in calendar.days}

    grid_start = by_date[date(2026, 8, 30)]
    assert grid_start.ending_balance == 1000.0
    assert grid_start.projected_count == 0
    assert grid_start.items == []

    visible_occurrence = by_date[date(2026, 8, 31)]
    assert visible_occurrence.ending_balance == 1000.0
    assert visible_occurrence.projected_count == 1
    assert visible_occurrence.projected_expense == 0.0
    assert visible_occurrence.items[0].recurring_id == recurring.id
    assert visible_occurrence.items[0].is_ignored is True
    assert by_date[date(2026, 9, 1)].ending_balance == 1000.0


@pytest.mark.asyncio
async def test_transaction_calendar_carries_more_than_occurrence_safety_limit(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Opening",
            amount=Decimal("1000"),
            currency="BRL",
            date=date(2019, 12, 1),
            type="credit",
            source="opening_balance",
        ),
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Long-running weekly debit",
            amount=Decimal("1"),
            currency="BRL",
            type="debit",
            frequency="weekly",
            start_date=date(2020, 1, 6),
            next_occurrence=date(2020, 1, 6),
        ),
    ])
    await session.commit()

    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 9, 1)
    )
    grid_start = next(day for day in calendar.days if day.date == date(2026, 8, 30))

    # 347 weekly occurrences precede the grid, exceeding the helper's 200-row
    # safety limit. Every one contributes to the carried balance, but no item.
    assert grid_start.ending_balance == 653.0
    assert grid_start.projected_count == 0
    assert grid_start.items == []


@pytest.mark.asyncio
async def test_transaction_calendar_ignored_actual_is_consistent_across_months(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    ignored_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        icon="eye-off",
        color="#64748b",
        is_ignored=True,
    )
    session.add_all([account, ignored_category])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Opening",
            amount=Decimal("1000"),
            currency="BRL",
            date=date(2026, 7, 1),
            type="credit",
            source="opening_balance",
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            category_id=ignored_category.id,
            description="Ignored debit",
            amount=Decimal("100"),
            currency="BRL",
            date=date(2026, 8, 3),
            type="debit",
            source="manual",
            is_ignored=False,
        ),
    ])
    await session.commit()

    august = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 8, 1)
    )
    september = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 9, 1)
    )
    august_by_date = {day.date: day for day in august.days}
    september_by_date = {day.date: day for day in september.days}

    august_september_1 = august_by_date[date(2026, 9, 1)]
    september_september_1 = september_by_date[date(2026, 9, 1)]
    assert august_september_1.ending_balance == 1000.0
    assert september_september_1.ending_balance == 1000.0
    assert august_september_1.ending_balance == september_september_1.ending_balance
