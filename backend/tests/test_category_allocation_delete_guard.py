"""Tests for category deletion when allocations reference the category.

Ensures:
1. get_category_usage counts allocation rows
2. delete_category with no transfer is blocked when only allocations reference it
3. delete_category with a transfer_to merges allocations correctly,
   including the unique-constraint conflict case.
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
    account = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="Wallet", type="checking", balance=Decimal("1000.00"), currency="USD",
    )
    session.add(account)
    await session.flush()
    return account


async def _make_tx(session, user_id, account_id, amount=Decimal("100.00")):
    tx = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        description="Test", amount=amount, currency="USD",
        date=date.today(), type="debit", source="manual",
    )
    session.add(tx)
    await session.flush()
    return tx


async def _make_category(session, user_id, workspace_id, name="Food"):
    cat = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name)
    session.add(cat)
    await session.flush()
    return cat


async def _make_allocations(session, tx, workspace_id, *cat_amount_pairs):
    """Create category allocations directly on a transaction."""
    from app.models.transaction_category_allocation import TransactionCategoryAllocation
    for i, (cat_id, amount) in enumerate(cat_amount_pairs):
        session.add(TransactionCategoryAllocation(
            transaction_id=tx.id, workspace_id=workspace_id,
            category_id=cat_id, amount=amount, position=i,
        ))
    await session.flush()


async def test_usage_counts_allocations(session: AsyncSession, test_user, test_workspace):
    """get_category_usage includes rows from transaction_category_allocations."""
    from app.services.category_service import get_category_usage

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("150.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")

    await _make_allocations(session, tx, test_workspace.id,
                            (food.id, Decimal("100.00")), (clothing.id, Decimal("50.00")))

    usage = await get_category_usage(session, test_workspace.id, food.id)
    assert usage.category_allocations >= 1
    assert not usage.is_empty


async def test_delete_blocked_when_only_allocations(session: AsyncSession, test_user, test_workspace):
    """delete_category raises CategoryInUseError when a category is only in allocations."""
    from app.services.category_service import delete_category
    from app.services.category_service import CategoryInUseError

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("150.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "FoodBlock")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "ClothingBlock")

    await _make_allocations(session, tx, test_workspace.id,
                            (food.id, Decimal("100.00")), (clothing.id, Decimal("50.00")))
    await session.commit()

    with pytest.raises(CategoryInUseError):
        await delete_category(session, food.id, test_workspace.id)


async def test_transfer_merges_allocation_rows(session: AsyncSession, test_user, test_workspace):
    """Transfer Food→Groceries on a tx that already has Groceries sums amounts."""
    from app.services.category_service import delete_category
    from app.models.transaction_category_allocation import TransactionCategoryAllocation
    from sqlalchemy import select

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("150.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "FoodMerge")
    groceries = await _make_category(session, test_user.id, test_workspace.id, "GroceriesMerge")

    # tx already has Groceries: 50.00 and Food: 100.00
    await _make_allocations(session, tx, test_workspace.id,
                            (groceries.id, Decimal("50.00")), (food.id, Decimal("100.00")))
    await session.commit()

    # Transfer Food → Groceries; Groceries row should become 150.00, Food row removed
    await delete_category(session, food.id, test_workspace.id, transfer_to_id=groceries.id)

    result = await session.execute(
        select(TransactionCategoryAllocation)
        .where(TransactionCategoryAllocation.transaction_id == tx.id)
    )
    rows = result.scalars().all()
    # Only one row remains (the merged Groceries row), Food row is gone
    assert len(rows) == 1
    assert rows[0].category_id == groceries.id
    assert rows[0].amount == Decimal("150.00")
