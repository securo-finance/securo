import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.schemas.budget import BudgetCreate, BudgetUpdate
from app.services.budget_service import (
    create_budget,
    delete_budget,
    get_budget,
    get_budget_vs_actual,
    get_budgets,
    update_budget,
)


# ---------------------------------------------------------------------------
# create_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_budget(session: AsyncSession, test_user, test_workspace, test_categories):
    data = BudgetCreate(
        category_id=test_categories[0].id,
        amount=Decimal("500.00"),
        month=date(2025, 3, 15),  # mid-month — should normalize to day=1
    )
    budget = await create_budget(session, test_workspace.id, test_user.id, data)

    assert budget.id is not None
    assert budget.amount == Decimal("500.00")
    assert budget.month == date(2025, 3, 1)
    assert budget.is_recurring is False


@pytest.mark.asyncio
async def test_create_recurring_budget(session: AsyncSession, test_user, test_workspace, test_categories):
    data = BudgetCreate(
        category_id=test_categories[0].id,
        amount=Decimal("300.00"),
        month=date(2025, 1, 1),
        is_recurring=True,
    )
    budget = await create_budget(session, test_workspace.id, test_user.id, data)

    assert budget.is_recurring is True
    assert budget.month == date(2025, 1, 1)


# ---------------------------------------------------------------------------
# get_budgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budgets_all(session: AsyncSession, test_user, test_workspace, test_categories):
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100"),
            month=date(2025, 1, 1),
        ),
    )
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[1].id,
            amount=Decimal("200"),
            month=date(2025, 2, 1),
        ),
    )

    budgets = await get_budgets(session, test_workspace.id)
    assert len(budgets) >= 2


@pytest.mark.asyncio
async def test_get_budgets_with_month_filter(session: AsyncSession, test_user, test_workspace, test_categories):
    # Recurring default for Jan
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100"),
            month=date(2025, 1, 1),
            is_recurring=True,
        ),
    )
    # Month-specific override for March
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("999"),
            month=date(2025, 3, 1),
            is_recurring=False,
        ),
    )

    # Querying March should get the override, not the recurring
    budgets = await get_budgets(session, test_workspace.id, month=date(2025, 3, 1))
    cat0_budgets = [b for b in budgets if b.category_id == test_categories[0].id]
    assert len(cat0_budgets) == 1
    assert cat0_budgets[0].amount == Decimal("999")
    assert cat0_budgets[0].is_recurring is False


@pytest.mark.asyncio
async def test_get_budgets_recurring_default_for_future(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    # Recurring from Jan carries forward to June
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[1].id,
            amount=Decimal("250"),
            month=date(2025, 1, 1),
            is_recurring=True,
        ),
    )

    budgets = await get_budgets(session, test_workspace.id, month=date(2025, 6, 1))
    cat1_budgets = [b for b in budgets if b.category_id == test_categories[1].id]
    assert len(cat1_budgets) == 1
    assert cat1_budgets[0].amount == Decimal("250")


# ---------------------------------------------------------------------------
# get_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budget_by_id(session: AsyncSession, test_user, test_workspace, test_categories):
    created = await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("400"),
            month=date(2025, 4, 1),
        ),
    )
    fetched = await get_budget(session, created.id, test_workspace.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_budget_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_budget(session, uuid.uuid4(), test_workspace.id)
    assert result is None


# ---------------------------------------------------------------------------
# update_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_budget_in_place(session: AsyncSession, test_user, test_workspace, test_categories):
    budget = await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100"),
            month=date(2025, 5, 1),
        ),
    )
    updated = await update_budget(
        session, budget.id, test_workspace.id, BudgetUpdate(amount=Decimal("777"))
    )

    assert updated is not None
    assert updated.id == budget.id
    assert updated.amount == Decimal("777")


@pytest.mark.asyncio
async def test_update_recurring_different_effective_month_creates_new(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    budget = await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100"),
            month=date(2025, 1, 1),
            is_recurring=True,
        ),
    )
    updated = await update_budget(
        session,
        budget.id,
        test_workspace.id,
        BudgetUpdate(amount=Decimal("200"), effective_month=date(2025, 6, 1)),
    )

    assert updated is not None
    assert updated.id != budget.id  # new record
    assert updated.month == date(2025, 6, 1)
    assert updated.amount == Decimal("200")
    assert updated.is_recurring is True


@pytest.mark.asyncio
async def test_update_budget_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_budget(
        session, uuid.uuid4(), test_workspace.id, BudgetUpdate(amount=Decimal("1"))
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_budget(session: AsyncSession, test_user, test_workspace, test_categories):
    budget = await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("50"),
            month=date(2025, 7, 1),
        ),
    )
    assert await delete_budget(session, budget.id, test_workspace.id) is True
    assert await get_budget(session, budget.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_budget_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_budget(session, uuid.uuid4(), test_workspace.id) is False


# ---------------------------------------------------------------------------
# get_budget_vs_actual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budget_vs_actual(session: AsyncSession, test_user, test_workspace, test_categories):
    # Need a category group for budget_vs_actual join
    group = CategoryGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="TestGroup",
        icon="folder",
        color="#000000",
        position=0,
        is_system=False,
    )
    session.add(group)
    await session.commit()

    # Assign group to category
    test_categories[0].group_id = group.id
    await session.commit()

    # Create account
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="BudgetAcc",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    # Create budget for March 2025
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("500"),
            month=date(2025, 3, 1),
        ),
    )

    # Create spending transaction in March
    txn = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=test_categories[0].id,
        description="IFOOD",
        amount=Decimal("100"),
        date=date(2025, 3, 10),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()

    comparisons = await get_budget_vs_actual(session, test_workspace.id, test_user.id, month=date(2025, 3, 1))
    assert len(comparisons) > 0

    cat0_comp = [c for c in comparisons if c.category_id == test_categories[0].id]
    assert len(cat0_comp) == 1
    assert cat0_comp[0].budget_amount == Decimal("500")
    assert cat0_comp[0].actual_amount == Decimal("100")
    assert cat0_comp[0].percentage_used == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_budget_vs_actual_excludes_transfers(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="BvAXfer",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    pair_id = uuid.uuid4()
    txns = [
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Transfer",
            amount=Decimal("200"),
            date=date(2025, 4, 5),
            type="debit",
            source="manual",
            transfer_pair_id=pair_id,
            created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Real spend",
            amount=Decimal("50"),
            date=date(2025, 4, 6),
            type="debit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
    ]
    session.add_all(txns)
    await session.commit()

    comparisons = await get_budget_vs_actual(session, test_workspace.id, test_user.id, month=date(2025, 4, 1))
    cat0 = [c for c in comparisons if c.category_id == test_categories[0].id]
    if cat0:
        assert cat0[0].actual_amount == Decimal("50")


@pytest.mark.asyncio
async def test_budget_vs_actual_includes_prev_month(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="PrevMonth",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    # Spending and a refund in Feb (prev month)
    txn_prev = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=test_categories[0].id,
        description="Feb spend",
        amount=Decimal("75"),
        date=date(2025, 2, 15),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    txn_prev_refund = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=test_categories[0].id,
        description="Feb refund",
        amount=Decimal("30"),
        date=date(2025, 2, 20),
        type="credit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    # Spending in March (current)
    txn_curr = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=test_categories[0].id,
        description="Mar spend",
        amount=Decimal("120"),
        date=date(2025, 3, 15),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([txn_prev, txn_prev_refund, txn_curr])
    await session.commit()

    comparisons = await get_budget_vs_actual(session, test_workspace.id, test_user.id, month=date(2025, 3, 1))
    cat0 = [c for c in comparisons if c.category_id == test_categories[0].id]
    assert len(cat0) == 1
    assert cat0[0].prev_month_amount == Decimal("45")


@pytest.mark.asyncio
async def test_budget_vs_actual_offsets_refunds(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="RefundAccount",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    # Budget of 200 for April 2025
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("200"),
            month=date(2025, 4, 1),
        ),
    )

    # 2 expenses: 100 and 50 (total 150)
    # 1 refund (credit): 30
    # Net actual should be: 150 - 30 = 120
    txns = [
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Previous month expense",
            amount=Decimal("80"),
            date=date(2025, 3, 10),
            type="debit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Previous month refund",
            amount=Decimal("25"),
            date=date(2025, 3, 12),
            type="credit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Expense 1",
            amount=Decimal("100"),
            date=date(2025, 4, 10),
            type="debit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Expense 2",
            amount=Decimal("50"),
            date=date(2025, 4, 12),
            type="debit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=test_categories[0].id,
            description="Refund",
            amount=Decimal("30"),
            date=date(2025, 4, 15),
            type="credit",
            source="manual",
            created_at=datetime.now(timezone.utc),
        ),
    ]
    session.add_all(txns)
    await session.commit()

    comparisons = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=date(2025, 4, 1)
    )
    cat0 = [c for c in comparisons if c.category_id == test_categories[0].id]
    assert len(cat0) == 1
    assert cat0[0].actual_amount == Decimal("120")
    assert cat0[0].prev_month_amount == Decimal("55")


# ---------------------------------------------------------------------------
# Rolling Budget Carryover (#192)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollover_first_month_and_cumulative_surplus_and_deficit(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    """Opt-in rollover starts at 0 carryover in the first enabled month, then
    chains cumulative surpluses and deficits into subsequent months, clamping
    percentage_used to None when effective_budget_amount <= 0."""
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: date(2025, 6, 15))
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: date(2025, 6, 15))

    cat = test_categories[0]
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="RolloverAcc",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()

    # Recurring budget starting Jan 2025 with rollover=True: 500/month
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id,
            amount=Decimal("500.00"),
            month=date(2025, 1, 1),
            is_recurring=True,
            rollover=True,
        ),
    )

    # Jan spend 300 -> surplus +200 -> Feb carryover +200, effective 700
    # Feb spend 400 -> cumulative +300 -> Mar carryover +300, effective 800
    # Mar spend 1400 -> cumulative -600 -> Apr carryover -600, effective -100 (percentage_used is None)
    txs = [
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=cat.id, description="Jan",
            amount=Decimal("300"), amount_primary=Decimal("300"),
            date=date(2025, 1, 10), type="debit", source="manual", currency="BRL",
            status="posted", created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=cat.id, description="Feb",
            amount=Decimal("400"), amount_primary=Decimal("400"),
            date=date(2025, 2, 10), type="debit", source="manual", currency="BRL",
            status="posted", created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=cat.id, description="Mar",
            amount=Decimal("1400"), amount_primary=Decimal("1400"),
            date=date(2025, 3, 10), type="debit", source="manual", currency="BRL",
            status="posted", created_at=datetime.now(timezone.utc),
        ),
    ]
    session.add_all(txs)
    await session.commit()

    jan = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 1, 1)) if c.category_id == cat.id)
    assert jan.budget_amount == Decimal("500.00")
    assert jan.carryover_amount == Decimal("0.00")
    assert jan.effective_budget_amount == Decimal("500.00")
    assert jan.percentage_used == pytest.approx(60.0)

    feb = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 2, 1)) if c.category_id == cat.id)
    assert feb.budget_amount == Decimal("500.00")
    assert feb.carryover_amount == Decimal("200.00")
    assert feb.effective_budget_amount == Decimal("700.00")
    assert feb.percentage_used == pytest.approx(57.1)

    mar = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 3, 1)) if c.category_id == cat.id)
    assert mar.carryover_amount == Decimal("300.00")
    assert mar.effective_budget_amount == Decimal("800.00")
    assert mar.percentage_used == pytest.approx(175.0)

    apr = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 4, 1)) if c.category_id == cat.id)
    assert apr.budget_amount == Decimal("500.00")
    assert apr.carryover_amount == Decimal("-600.00")
    assert apr.effective_budget_amount == Decimal("-100.00")
    assert apr.percentage_used is None


@pytest.mark.asyncio
async def test_rollover_reset_boundary_on_recurring_split_resumes_next_month(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    """reset_rollover=True on a recurring budget split at month M clears carryover
    for month M only, and resumes chaining M's surplus into M+1."""
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: date(2025, 6, 15))
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: date(2025, 6, 15))

    cat = test_categories[0]
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="ResetAcc", type="checking", balance=Decimal("5000"), currency="BRL",
    )
    session.add(account)
    await session.flush()

    b_jan = await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id,
            amount=Decimal("500.00"),
            month=date(2025, 1, 1),
            is_recurring=True,
            rollover=True,
        ),
    )
    # Split recurring budget at Feb with reset_rollover=True
    b_feb = await update_budget(
        session,
        b_jan.id,
        test_workspace.id,
        BudgetUpdate(amount=Decimal("500.00"), effective_month=date(2025, 2, 1), reset_rollover=True),
    )
    assert b_feb is not None
    assert b_feb.rollover is True
    assert b_feb.reset_rollover is True

    # Jan spend 100 (+400 surplus ignored due to Feb reset), Feb spend 200 (+300 surplus -> Mar)
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=cat.id, description="Jan",
            amount=Decimal("100"), amount_primary=Decimal("100"),
            date=date(2025, 1, 10), type="debit", source="manual", currency="BRL",
            status="posted", created_at=datetime.now(timezone.utc),
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=cat.id, description="Feb",
            amount=Decimal("200"), amount_primary=Decimal("200"),
            date=date(2025, 2, 10), type="debit", source="manual", currency="BRL",
            status="posted", created_at=datetime.now(timezone.utc),
        ),
    ])
    await session.commit()

    feb = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 2, 1)) if c.category_id == cat.id)
    assert feb.reset_rollover is True
    assert feb.carryover_amount == Decimal("0.00")
    assert feb.effective_budget_amount == Decimal("500.00")

    mar = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 3, 1)) if c.category_id == cat.id)
    assert mar.reset_rollover is False
    assert mar.carryover_amount == Decimal("300.00")
    assert mar.effective_budget_amount == Decimal("800.00")


@pytest.mark.asyncio
async def test_rollover_tombstone_and_opt_out_and_24_month_lookback_cap(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    """A zero tombstone breaks the chain, rollover=False suppresses carryover,
    and lookback is bounded by MAX_ROLLOVER_LOOKBACK_MONTHS (24)."""
    from app.models.budget import Budget
    from app.services.budget_service import MAX_ROLLOVER_LOOKBACK_MONTHS, _add_months, get_budget_rollover_summary

    assert MAX_ROLLOVER_LOOKBACK_MONTHS == 24
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: date(2027, 6, 15))
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: date(2027, 6, 15))

    cat_break = test_categories[0]
    cat_cap = test_categories[1]

    # cat_break: Jan=500(rollover), Feb=0(tombstone), Mar=500(rollover) -> Mar starts at 0
    await create_budget(session, test_workspace.id, test_user.id, BudgetCreate(category_id=cat_break.id, amount=Decimal("500"), month=date(2025, 1, 1), rollover=True))
    session.add(
        Budget(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            category_id=cat_break.id,
            amount=Decimal("0"),
            currency="BRL",
            month=date(2025, 2, 1),
            is_recurring=False,
            rollover=False,
        )
    )
    await session.commit()
    await create_budget(session, test_workspace.id, test_user.id, BudgetCreate(category_id=cat_break.id, amount=Decimal("500"), month=date(2025, 3, 1), rollover=True))

    mar = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 3, 1)) if c.category_id == cat_break.id)
    assert mar.carryover_amount == Decimal("0.00")

    # Tombstone month excludes 0-amount category from rollover-summary
    feb_summary = await get_budget_rollover_summary(session, test_workspace.id, test_user.id, date(2025, 2, 1))
    assert all(c["category_id"] != cat_break.id for c in feb_summary["categories"])

    # cat_cap: 26 months of 100/month recurring rollover with 0 spend -> capped at 24 * 100 = 2400
    target = date(2027, 3, 1)
    start_26_back = _add_months(target, -26)
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=cat_cap.id, amount=Decimal("100"), month=start_26_back, is_recurring=True, rollover=True),
    )
    cap_cmp = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, target) if c.category_id == cat_cap.id)
    assert cap_cmp.carryover_amount == Decimal("2400.00")
    assert cap_cmp.effective_budget_amount == Decimal("2500.00")

    # Target override with rollover=False suppresses carryover
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=cat_cap.id, amount=Decimal("100"), month=target, is_recurring=False, rollover=False),
    )
    suppressed = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, target) if c.category_id == cat_cap.id)
    assert suppressed.carryover_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_rollover_carryover_respects_accrual_mode_split_offsets_and_shared_shares(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    """Carryover attributes accrual CC purchases + owner split offsets by
    effective_date, includes cross-workspace shared spending, and includes
    recurring projections only when month_end > app_today()."""
    import bcrypt as _bcrypt
    from app.models.group import Group, GroupMember
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.transaction_split import TransactionSplit
    from app.models.user import User
    from app.services.admin_service import set_app_setting
    from app.services.workspace_service import create_workspace

    # Today = 2025-05-15 -> Mar & Apr are past (no unmaterialized recurring projections),
    # May is in-progress (month_end > app_today(), so May recurring projections count into Jun!)
    fixed_today = date(2025, 5, 15)
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: fixed_today)
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: fixed_today)
    monkeypatch.setattr("app.services.dashboard_service.app_today", lambda: fixed_today)

    await set_app_setting(session, "credit_card_accounting_mode", "accrual")

    food = test_categories[0]
    await create_budget(
        session, test_workspace.id, test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("500"), month=date(2025, 3, 1), is_recurring=True, rollover=True),
    )

    cc = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="AccrualCC", type="credit_card", balance=Decimal("0"), currency="BRL",
        statement_close_day=25, payment_due_day=10,
    )
    bob = User(
        id=uuid.uuid4(), email="bob_rollover@example.com",
        hashed_password=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
        is_active=True, is_verified=True, preferences={"currency_display": "BRL"},
    )
    session.add_all([cc, bob])
    await session.flush()

    bob_ws = await create_workspace(session, name="BobRolloverWS", creator=bob, self_membership=True, seed_defaults=False)
    bob_acc = Account(id=uuid.uuid4(), user_id=bob.id, workspace_id=bob_ws.id, name="BobAcc", type="checking", balance=Decimal("0"), currency="BRL")
    group = Group(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="RolloverGroup")
    session.add_all([bob_acc, group])
    await session.flush()

    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Me", is_self=True)
    bob_m = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Bob", linked_user_id=bob.id)
    session.add_all([me, bob_m])
    await session.flush()

    # Mar 28 CC purchase 200 split 100/100 with Bob -> effective Apr 10 (accrual shifts both tx & split offset to Apr!)
    tx_own = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=cc.id, category_id=food.id, description="Mar28 Dinner",
        amount=Decimal("200"), amount_primary=Decimal("200"),
        date=date(2025, 3, 28), effective_date=date(2025, 4, 10),
        type="debit", source="manual", currency="BRL", status="posted",
        created_at=datetime.now(timezone.utc),
    )
    # Apr 12 Bob shared expense 80 (user share 40) in food category
    tx_bob = Transaction(
        id=uuid.uuid4(), user_id=bob.id, workspace_id=bob_ws.id,
        account_id=bob_acc.id, category_id=food.id, description="Apr Shared",
        amount=Decimal("80"), amount_primary=Decimal("80"),
        date=date(2025, 4, 12), type="debit", source="manual", currency="BRL",
        status="posted", created_at=datetime.now(timezone.utc),
    )
    session.add_all([tx_own, tx_bob])
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx_own.id, workspace_id=test_workspace.id, group_member_id=me.id, share_amount=Decimal("100")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx_own.id, workspace_id=test_workspace.id, group_member_id=bob_m.id, share_amount=Decimal("100")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx_bob.id, workspace_id=bob_ws.id, group_member_id=me.id, share_amount=Decimal("40")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx_bob.id, workspace_id=bob_ws.id, group_member_id=bob_m.id, share_amount=Decimal("40")),
    ])
    # Recurring template starting Mar 2025 (100/mo on 20th):
    # Mar & Apr are past (<= today) so unmaterialized recurring does NOT count in Mar/Apr.
    # May is in-progress (month_end > today) so Apr 20 (shifted to May 10 due date)
    # counts as May recurring spend (100), reducing Jun carryover to 860 + 500 - 100 = 1260,
    # while May 20 shifts to Jun 10 so Jun projected_amount = 100.
    session.add(RecurringTransaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=cc.id,
        category_id=food.id, description="RecFood", amount=Decimal("100"),
        currency="BRL", type="debit", frequency="monthly",
        start_date=date(2025, 3, 20), next_occurrence=date(2025, 4, 20), is_active=True,
    ))
    await session.commit()

    # Mar: base=500, spend=0 -> Apr carryover = +500
    apr = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 4, 1)) if c.category_id == food.id)
    assert apr.carryover_amount == Decimal("500.00")

    # Apr: base=500, spend=(200-100)+40=140 -> May carryover = 500 + 500 - 140 = 860
    may = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 5, 1)) if c.category_id == food.id)
    assert may.carryover_amount == Decimal("860.00")

    # May: base=500, spend=100 (Apr 20 shifted to May 10) -> Jun carryover = 860 + 500 - 100 = 1260
    jun = next(c for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 6, 1)) if c.category_id == food.id)
    assert jun.carryover_amount == Decimal("1260.00")
    assert jun.projected_amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_budget_outputs_quantize_repeating_decimal_splits_to_two_places(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    """Repeating-decimal split scaling (e.g. 10 * 100 / 30 = 33.3333...) must
    quantize to Decimal('0.01') (ROUND_HALF_UP) across comparison, carryover,
    rollover summary, and multi-month forecast."""
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit
    from app.services.budget_service import (
        get_budget_rollover_summary,
        get_multi_month_forecast,
    )

    fixed_today = date(2025, 4, 15)
    monkeypatch.setattr("app.services.budget_service.app_today", lambda: fixed_today)
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: fixed_today)
    monkeypatch.setattr("app.services.dashboard_service.app_today", lambda: fixed_today)

    food = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=food.id,
            amount=Decimal("200.00"),
            month=date(2025, 3, 1),
            is_recurring=True,
            rollover=True,
        ),
    )

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="QuantAcc",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    group = Group(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="QuantGroup"
    )
    session.add_all([acc, group])
    await session.flush()
    me = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Me", is_self=True
    )
    friend = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Friend"
    )
    session.add_all([me, friend])
    await session.flush()

    # Mar posted tx: amount=30 USD, amount_primary=100 BRL, friend share=10 USD ->
    # offset = 10 * 100 / 30 = 33.3333... -> Mar spend = 66.67,
    # Apr carryover = 200.00 - 66.67 = 133.33.
    tx_mar = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=acc.id,
        category_id=food.id,
        description="Mar Repeating",
        amount=Decimal("30.00"),
        amount_primary=Decimal("100.00"),
        date=date(2025, 3, 10),
        type="debit",
        source="manual",
        currency="USD",
        status="posted",
        created_at=datetime.now(timezone.utc),
    )
    # Apr pending forecast tx: amount=30 USD, amount_primary=100 BRL, friend share=10 USD ->
    # owner fraction = 2/3 -> 66.6666... -> quantized 66.67.
    tx_apr_pending = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=acc.id,
        category_id=food.id,
        description="Apr Pending Repeating",
        amount=Decimal("30.00"),
        amount_primary=Decimal("100.00"),
        date=date(2025, 4, 20),
        type="debit",
        source="manual",
        currency="USD",
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([tx_mar, tx_apr_pending])
    await session.flush()
    session.add_all([
        TransactionSplit(
            id=uuid.uuid4(), transaction_id=tx_mar.id, workspace_id=test_workspace.id,
            group_member_id=me.id, share_amount=Decimal("20.00"),
        ),
        TransactionSplit(
            id=uuid.uuid4(), transaction_id=tx_mar.id, workspace_id=test_workspace.id,
            group_member_id=friend.id, share_amount=Decimal("10.00"),
        ),
        TransactionSplit(
            id=uuid.uuid4(), transaction_id=tx_apr_pending.id, workspace_id=test_workspace.id,
            group_member_id=me.id, share_amount=Decimal("20.00"),
        ),
        TransactionSplit(
            id=uuid.uuid4(), transaction_id=tx_apr_pending.id, workspace_id=test_workspace.id,
            group_member_id=friend.id, share_amount=Decimal("10.00"),
        ),
    ])
    await session.commit()

    apr = next(
        c
        for c in await get_budget_vs_actual(session, test_workspace.id, test_user.id, date(2025, 4, 1))
        if c.category_id == food.id
    )
    assert str(apr.carryover_amount) == "133.33"
    assert str(apr.effective_budget_amount) == "333.33"
    assert str(apr.projected_amount) == "66.67"
    assert str(apr.prev_month_amount) == "66.67"

    summary = await get_budget_rollover_summary(
        session, test_workspace.id, test_user.id, date(2025, 4, 1)
    )
    assert str(summary["total_carryover"]) == "133.33"
    assert str(summary["total_effective_budget"]) == "333.33"
    assert str(summary["total_projected_spent"]) == "66.67"
    assert str(summary["net_surplus"]) == "266.66"

    forecast = await get_multi_month_forecast(
        session, test_workspace.id, test_user.id, start_month=date(2025, 4, 1), num_months=2
    )
    f_apr, f_may = forecast["items"]
    assert str(f_apr["carryover_balance"]) == "133.33"
    assert str(f_apr["effective_budget"]) == "333.33"
    assert str(f_apr["projected_spend"]) == "66.67"
    assert str(f_apr["projected_variance"]) == "266.66"
    assert str(f_may["carryover_balance"]) == "266.66"
    assert str(f_may["effective_budget"]) == "466.66"


@pytest.mark.asyncio
async def test_get_budgets_projects_reset_rollover_false_on_inherited_months_without_poisoning_session(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """GET /api/budgets?month=M must return reset_rollover=False for recurring
    rows inherited from an earlier month where reset_rollover=True, while keeping
    reset_rollover=True for the originating month in the same session."""
    cat = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id,
            amount=Decimal("400.00"),
            month=date(2025, 3, 1),
            is_recurring=True,
            rollover=True,
            reset_rollover=True,
        ),
    )

    # Inherited April view must project reset_rollover=False without dirtying the session.
    apr_rows = await get_budgets(session, test_workspace.id, month=date(2025, 4, 1))
    apr_cat = next(b for b in apr_rows if b.category_id == cat.id)
    assert apr_cat.reset_rollover is False
    assert not session.dirty

    # Originating March view in the same session must still see reset_rollover=True.
    mar_rows = await get_budgets(session, test_workspace.id, month=date(2025, 3, 1))
    mar_cat = next(b for b in mar_rows if b.category_id == cat.id)
    assert mar_cat.reset_rollover is True

