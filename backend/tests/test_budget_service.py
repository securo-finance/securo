from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
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


@pytest.mark.asyncio
async def test_create_budget_sets_category_budget_state(
    session: AsyncSession, test_user, test_categories
):
    budget = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("500.00"),
            month=date(2025, 3, 15),
            is_recurring=True,
        ),
    )

    await session.refresh(test_categories[0])
    assert budget.amount == Decimal("500.00")
    assert budget.month == date(2025, 3, 1)
    assert budget.is_recurring is False
    assert test_categories[0].has_budget is True
    assert test_categories[0].budget_amount == Decimal("500.00")


@pytest.mark.asyncio
async def test_create_budget_collapses_existing_rows_for_same_category(
    session: AsyncSession, test_user, test_categories
):
    first = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100.00"),
            month=date(2025, 1, 1),
        ),
    )
    second = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("250.00"),
            month=date(2025, 6, 1),
        ),
    )

    budgets = await get_budgets(session, test_user.id)
    cat_budgets = [b for b in budgets if b.category_id == test_categories[0].id]
    assert len(cat_budgets) == 1
    assert cat_budgets[0].id == first.id == second.id
    assert cat_budgets[0].amount == Decimal("250.00")


@pytest.mark.asyncio
async def test_get_budgets_ignores_month_filter_and_returns_current_rows(
    session: AsyncSession, test_user, test_categories
):
    await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("300.00"),
            month=date(2025, 2, 1),
        ),
    )

    budgets = await get_budgets(session, test_user.id, month=date(2026, 2, 1))
    assert len([b for b in budgets if b.category_id == test_categories[0].id]) == 1


@pytest.mark.asyncio
async def test_update_budget_syncs_category_budget_state(
    session: AsyncSession, test_user, test_categories
):
    budget = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("100.00"),
            month=date(2025, 5, 1),
        ),
    )

    updated = await update_budget(
        session,
        budget.id,
        test_user.id,
        BudgetUpdate(amount=Decimal("777.00"), effective_month=date(2025, 8, 1)),
    )

    await session.refresh(test_categories[0])
    assert updated is not None
    assert updated.id == budget.id
    assert updated.amount == Decimal("777.00")
    assert updated.month == date(2025, 8, 1)
    assert updated.is_recurring is False
    assert test_categories[0].has_budget is True
    assert test_categories[0].budget_amount == Decimal("777.00")


@pytest.mark.asyncio
async def test_get_budget_by_id(session: AsyncSession, test_user, test_categories):
    created = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("400.00"),
            month=date(2025, 4, 1),
        ),
    )

    fetched = await get_budget(session, created.id, test_user.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_delete_budget_clears_category_budget_state(
    session: AsyncSession, test_user, test_categories
):
    budget = await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("50.00"),
            month=date(2025, 7, 1),
        ),
    )

    assert await delete_budget(session, budget.id, test_user.id) is True
    await session.refresh(test_categories[0])
    assert await get_budget(session, budget.id, test_user.id) is None
    assert test_categories[0].has_budget is False
    assert test_categories[0].budget_amount is None


@pytest.mark.asyncio
async def test_get_budget_vs_actual_reads_category_budget_state(
    session: AsyncSession, test_user, test_categories
):
    account = Account(
        user_id=test_user.id,
        connection_id=None,
        external_id=None,
        name="Checking",
        type="checking",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()

    txn = Transaction(
        user_id=test_user.id,
        account_id=account.id,
        category_id=test_categories[0].id,
        description="Market",
        amount=Decimal("120.00"),
        date=date(2025, 3, 5),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()

    await create_budget(
        session,
        test_user.id,
        BudgetCreate(
            category_id=test_categories[0].id,
            amount=Decimal("500.00"),
            month=date(2025, 3, 1),
        ),
    )

    comparisons = await get_budget_vs_actual(session, test_user.id, month=date(2025, 3, 1))
    cat_comp = [c for c in comparisons if c.category_id == test_categories[0].id]
    assert len(cat_comp) == 1
    assert cat_comp[0].budget_amount == Decimal("500.00")
    assert cat_comp[0].actual_amount == Decimal("120.00")
    assert cat_comp[0].is_recurring is False
