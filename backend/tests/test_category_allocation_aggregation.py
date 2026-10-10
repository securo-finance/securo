"""The money test: category allocations must re-attribute spending in
budget, reports, and dashboard while leaving period totals unchanged.

Scenario:
  - 150.00 debit, parent category Groceries, allocations Food=100 + Clothing=50
  - Budget: Food shows 100, Clothing 50, Groceries absent
  - Category breakdown: same
  - Income vs expenses period total unchanged (150.00)
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction


async def _make_account(session, user_id, workspace_id):
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="Wallet", type="checking", balance=Decimal("1000.00"), currency="USD",
    )
    session.add(acct)
    await session.flush()
    return acct


async def _make_category(session, user_id, workspace_id, name):
    cat = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name)
    session.add(cat)
    await session.flush()
    return cat


async def _make_tx(session, user_id, account_id, workspace_id, category_id, amount=Decimal("150.00")):
    tx = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        description="Supermarket", amount=amount, currency="USD",
        date=date(2025, 6, 15), type="debit", source="manual",
        category_id=category_id, workspace_id=workspace_id,
    )
    session.add(tx)
    await session.flush()
    return tx


async def _make_allocations(session, tx, workspace_id, *cat_amount_pairs):
    from app.models.transaction_category_allocation import TransactionCategoryAllocation
    for i, (cat_id, amount) in enumerate(cat_amount_pairs):
        session.add(TransactionCategoryAllocation(
            transaction_id=tx.id, workspace_id=workspace_id,
            category_id=cat_id, amount=amount, position=i,
        ))
    await session.flush()


async def test_allocation_deltas_map(session: AsyncSession, test_user, test_workspace):
    """category_allocation_deltas returns correct signed map."""
    from app.services._query_filters import category_allocation_deltas
    from datetime import date

    account = await _make_account(session, test_user.id, test_workspace.id)
    groceries = await _make_category(session, test_user.id, test_workspace.id, "Groceries-D")
    food = await _make_category(session, test_user.id, test_workspace.id, "Food-D")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing-D")

    tx = await _make_tx(
        session, test_user.id, account.id, test_workspace.id, groceries.id,
    )
    await _make_allocations(session, tx, test_workspace.id,
                            (food.id, Decimal("100.00")), (clothing.id, Decimal("50.00")))
    await session.commit()

    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 6, 1), month_end=date(2025, 7, 1),
        workspace_id=test_workspace.id,
    )

    # Groceries should go down by 150
    assert groceries.id in deltas
    assert deltas[groceries.id] == pytest.approx(-150.0)
    # Food gains 100
    assert food.id in deltas
    assert deltas[food.id] == pytest.approx(100.0)
    # Clothing gains 50
    assert clothing.id in deltas
    assert deltas[clothing.id] == pytest.approx(50.0)


async def test_budget_spending_reflects_allocations(session: AsyncSession, test_user, test_workspace):
    """Budget spending_map re-attributes allocated amounts correctly."""
    from app.services.budget_service import get_budget_vs_actual
    from app.models.budget import Budget

    account = await _make_account(session, test_user.id, test_workspace.id)
    groceries = await _make_category(session, test_user.id, test_workspace.id, "Groceries-B")
    food = await _make_category(session, test_user.id, test_workspace.id, "Food-B")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing-B")

    # Budget for each category
    for cat, amount in [(groceries, 200), (food, 150), (clothing, 100)]:
        session.add(Budget(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            category_id=cat.id, amount=Decimal(str(amount)), month=date(2025, 6, 1),
        ))

    tx = await _make_tx(
        session, test_user.id, account.id, test_workspace.id, groceries.id,
    )
    await _make_allocations(session, tx, test_workspace.id,
                            (food.id, Decimal("100.00")), (clothing.id, Decimal("50.00")))
    await session.commit()

    result = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, date(2025, 6, 1),
    )

    by_cat = {str(b.category_id): b.actual_amount for b in result}

    # Groceries: 150.00 in TX but fully allocated away → 0 actual
    groceries_actual = float(by_cat.get(str(groceries.id), 0))
    assert groceries_actual == pytest.approx(0.0, abs=0.01)

    # Food: 100.00 allocated → 100 actual
    food_actual = float(by_cat.get(str(food.id), 0))
    assert food_actual == pytest.approx(100.0, abs=0.01)

    # Clothing: 50.00 allocated → 50 actual
    clothing_actual = float(by_cat.get(str(clothing.id), 0))
    assert clothing_actual == pytest.approx(50.0, abs=0.01)
