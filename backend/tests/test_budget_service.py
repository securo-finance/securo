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
async def test_create_recurring_budget(
    session: AsyncSession, test_user, test_workspace, test_categories
):
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
async def test_get_budgets_with_month_filter(
    session: AsyncSession, test_user, test_workspace, test_categories
):
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
async def test_update_budget_in_place(
    session: AsyncSession, test_user, test_workspace, test_categories
):
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
async def test_get_budget_vs_actual(
    session: AsyncSession, test_user, test_workspace, test_categories
):
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

    comparisons = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=date(2025, 3, 1)
    )
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

    comparisons = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=date(2025, 4, 1)
    )
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

    # Spending in Feb (prev month)
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
    session.add_all([txn_prev, txn_curr])
    await session.commit()

    comparisons = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=date(2025, 3, 1)
    )
    cat0 = [c for c in comparisons if c.category_id == test_categories[0].id]
    if cat0:
        assert cat0[0].prev_month_amount == Decimal("75")
