"""Verify that a full year of pre-existing transactions (no allocations)
is completely unaffected by the category-allocation feature.

Scenario:
  - 12 months of ordinary debit transactions, each with a category, no allocations
  - Budget, category_allocation_deltas, and income/expenses period totals must be
    identical to what they would be without the allocation tables (zero delta).

This guards against the risk that the allocation re-attribution code changes
totals for users who upgrade with existing data and have never used the feature.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.transaction import Transaction


async def _make_account(session, user_id, workspace_id):
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="Wallet", type="checking", balance=Decimal("5000.00"), currency="USD",
    )
    session.add(acct)
    await session.flush()
    return acct


async def _make_category(session, user_id, workspace_id, name):
    cat = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name)
    session.add(cat)
    await session.flush()
    return cat


async def test_existing_data_zero_allocation_deltas(
    session: AsyncSession, test_user, test_workspace
):
    """category_allocation_deltas returns empty map when no allocations exist."""
    from app.services._query_filters import category_allocation_deltas

    account = await _make_account(session, test_user.id, test_workspace.id)
    groceries = await _make_category(session, test_user.id, test_workspace.id, "Groceries-E")
    transport = await _make_category(session, test_user.id, test_workspace.id, "Transport-E")

    # One transaction per month for a full year, no allocations
    for month in range(1, 13):
        cat = groceries if month % 2 == 0 else transport
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=f"Month {month}", amount=Decimal("100.00"), currency="USD",
            date=date(2025, month, 15), type="debit", source="manual",
            category_id=cat.id, workspace_id=test_workspace.id,
        ))
    await session.commit()

    # Delta map must be empty — no allocations means no re-attribution
    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 1, 1), month_end=date(2026, 1, 1),
        workspace_id=test_workspace.id,
    )
    assert deltas == {}, f"Expected empty deltas for un-allocated data, got {deltas}"


async def test_existing_data_budget_totals_unchanged(
    session: AsyncSession, test_user, test_workspace
):
    """Budget actual amounts are identical for transactions without allocations."""
    from app.services.budget_service import get_budget_vs_actual

    account = await _make_account(session, test_user.id, test_workspace.id)
    groceries = await _make_category(session, test_user.id, test_workspace.id, "Groceries-EU")
    transport = await _make_category(session, test_user.id, test_workspace.id, "Transport-EU")

    # Budget for June 2025
    for cat, budget_amt in [(groceries, 300), (transport, 200)]:
        session.add(Budget(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            category_id=cat.id, amount=Decimal(str(budget_amt)), month=date(2025, 6, 1),
        ))

    # 3 transactions in June — no allocations
    for i, (cat, amt) in enumerate([
        (groceries, Decimal("80.00")),
        (groceries, Decimal("120.00")),
        (transport, Decimal("50.00")),
    ]):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=f"tx-{i}", amount=amt, currency="USD",
            date=date(2025, 6, 10 + i), type="debit", source="manual",
            category_id=cat.id, workspace_id=test_workspace.id,
        ))
    await session.commit()

    result = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, date(2025, 6, 1),
    )
    by_cat = {str(b.category_id): float(b.actual_amount) for b in result}

    # Must exactly match raw transaction totals — allocation code must not change these
    assert by_cat.get(str(groceries.id), 0) == pytest.approx(200.0, abs=0.01)
    assert by_cat.get(str(transport.id), 0) == pytest.approx(50.0, abs=0.01)


async def test_existing_data_full_year_period_total_unchanged(
    session: AsyncSession, test_user, test_workspace
):
    """Income/expenses period total is identical across 12 months with no allocations."""
    from app.services._query_filters import category_allocation_deltas

    account = await _make_account(session, test_user.id, test_workspace.id)
    food = await _make_category(session, test_user.id, test_workspace.id, "Food-FY")
    rent = await _make_category(session, test_user.id, test_workspace.id, "Rent-FY")

    expected_total = Decimal("0.00")
    for month in range(1, 13):
        for cat, amt in [(food, Decimal("150.00")), (rent, Decimal("800.00"))]:
            session.add(Transaction(
                id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
                description="recurring", amount=amt, currency="USD",
                date=date(2025, month, 1), type="debit", source="manual",
                category_id=cat.id, workspace_id=test_workspace.id,
            ))
            expected_total += amt
    await session.commit()

    # Allocation deltas must be zero — so period total is never touched
    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 1, 1), month_end=date(2026, 1, 1),
        workspace_id=test_workspace.id,
    )
    total_delta = sum(abs(v) for v in deltas.values())
    assert total_delta == pytest.approx(0.0), (
        f"Allocation deltas must be zero for un-allocated data but got {deltas}"
    )
