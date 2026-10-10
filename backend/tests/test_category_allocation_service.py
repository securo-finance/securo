"""Tests for category_allocation_service — the core of the category-split feature.

Mirrors the structure and spirit of test_split_service.py.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_account(session: AsyncSession, user_id, workspace_id) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name="Wallet",
        type="checking",
        balance=Decimal("1000.00"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    return account


async def _make_tx(session: AsyncSession, user_id, account_id, amount: Decimal) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description="Test",
        amount=amount,
        currency="USD",
        date=date.today(),
        type="debit",
        source="manual",
    )
    session.add(tx)
    await session.flush()
    return tx


async def _make_category(session: AsyncSession, user_id, workspace_id, name="Food") -> Category:
    cat = Category(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
    )
    session.add(cat)
    await session.flush()
    return cat


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_replace_category_allocations_creates_rows(session: AsyncSession, test_user, test_workspace):
    """Two allocations summing to the transaction amount are saved correctly."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("150.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("100.00")),
            CategoryAllocationInput(category_id=clothing.id, amount=Decimal("50.00")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, payload, test_user.id
    )
    await session.flush()

    await session.refresh(tx, ["category_allocations"])
    assert len(tx.category_allocations) == 2
    amounts = {str(a.category_id): a.amount for a in tx.category_allocations}
    assert amounts[str(food.id)] == Decimal("100.00")
    assert amounts[str(clothing.id)] == Decimal("50.00")


async def test_sum_mismatch_rejected(session: AsyncSession, test_user, test_workspace):
    """Allocations that don't sum to the transaction amount are rejected."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("150.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("100.00")),
            CategoryAllocationInput(category_id=clothing.id, amount=Decimal("40.00")),  # 140 ≠ 150
        ]
    )
    with pytest.raises(ValueError, match="sum to the transaction amount"):
        await category_allocation_service.replace_category_allocations(
            session, tx, payload, test_user.id
        )


async def test_single_part_rejected(session: AsyncSession, test_user, test_workspace):
    """A single allocation (trivially equals the parent category) is rejected."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("100.00")),
        ]
    )
    with pytest.raises(ValueError, match="at least 2"):
        await category_allocation_service.replace_category_allocations(
            session, tx, payload, test_user.id
        )


async def test_duplicate_category_rejected(session: AsyncSession, test_user, test_workspace):
    """The same category appearing twice in the payload is rejected."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("60.00")),
            CategoryAllocationInput(category_id=food.id, amount=Decimal("40.00")),
        ]
    )
    with pytest.raises(ValueError, match="at most once"):
        await category_allocation_service.replace_category_allocations(
            session, tx, payload, test_user.id
        )


async def test_replace_clears_old_rows(session: AsyncSession, test_user, test_workspace):
    """Re-submitting a payload replaces all previous allocations."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")
    transport = await _make_category(session, test_user.id, test_workspace.id, "Transport")

    first = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("60.00")),
            CategoryAllocationInput(category_id=clothing.id, amount=Decimal("40.00")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, first, test_user.id
    )
    await session.flush()

    second = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("70.00")),
            CategoryAllocationInput(category_id=transport.id, amount=Decimal("30.00")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, second, test_user.id
    )
    await session.flush()

    await session.refresh(tx, ["category_allocations"])
    assert len(tx.category_allocations) == 2
    cats = {str(a.category_id) for a in tx.category_allocations}
    assert str(food.id) in cats
    assert str(transport.id) in cats
    assert str(clothing.id) not in cats


async def test_none_payload_leaves_rows_untouched(session: AsyncSession, test_user, test_workspace):
    """Passing payload=None performs no change."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("60.00")),
            CategoryAllocationInput(category_id=clothing.id, amount=Decimal("40.00")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, payload, test_user.id
    )
    await session.flush()

    # Now pass None — rows should be unchanged
    await category_allocation_service.replace_category_allocations(
        session, tx, None, test_user.id
    )
    await session.refresh(tx, ["category_allocations"])
    assert len(tx.category_allocations) == 2


async def test_empty_payload_clears_rows(session: AsyncSession, test_user, test_workspace):
    """Passing an empty allocations list removes all existing rows."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    food = await _make_category(session, test_user.id, test_workspace.id, "Food")
    clothing = await _make_category(session, test_user.id, test_workspace.id, "Clothing")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=food.id, amount=Decimal("60.00")),
            CategoryAllocationInput(category_id=clothing.id, amount=Decimal("40.00")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, payload, test_user.id
    )
    await session.flush()

    clear = CategoryAllocationsInput(allocations=[])
    await category_allocation_service.replace_category_allocations(
        session, tx, clear, test_user.id
    )
    await session.flush()

    await session.refresh(tx, ["category_allocations"])
    assert len(tx.category_allocations) == 0


async def test_rounding_residual_assigned_to_last(session: AsyncSession, test_user, test_workspace):
    """Three equal thirds of 10.00 — rounding residual goes to last row, sum exact."""
    from app.schemas.transaction_category_allocation import (
        CategoryAllocationInput,
        CategoryAllocationsInput,
    )
    from app.services import category_allocation_service

    account = await _make_account(session, test_user.id, test_workspace.id)
    # 10.00 / 3 = 3.33 each; 3.33+3.33+3.34 = 10.00
    tx = await _make_tx(session, test_user.id, account.id, Decimal("10.00"))
    c1 = await _make_category(session, test_user.id, test_workspace.id, "C1")
    c2 = await _make_category(session, test_user.id, test_workspace.id, "C2")
    c3 = await _make_category(session, test_user.id, test_workspace.id, "C3")

    payload = CategoryAllocationsInput(
        allocations=[
            CategoryAllocationInput(category_id=c1.id, amount=Decimal("3.33")),
            CategoryAllocationInput(category_id=c2.id, amount=Decimal("3.33")),
            # Last row: 10.00 - 3.33 - 3.33 = 3.34 — service should use 3.34 here
            CategoryAllocationInput(category_id=c3.id, amount=Decimal("3.34")),
        ]
    )
    await category_allocation_service.replace_category_allocations(
        session, tx, payload, test_user.id
    )
    await session.flush()

    await session.refresh(tx, ["category_allocations"])
    total = sum(a.amount for a in tx.category_allocations)
    assert total == Decimal("10.00")
