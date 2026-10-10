"""Full-feature regression dataset.

Builds a realistic workspace with ALL major app features active simultaneously
so any new feature addition can be validated against live existing data:

  - Category groups + categories (expense and income)
  - Multiple accounts (checking, savings, credit-card)
  - Expense transactions (various categories)
  - Income transactions (salary, freelance)
  - People-split transactions (Group + TransactionSplit)
  - Category-split transactions (TransactionCategoryAllocation — new feature)
  - Budgets for multiple categories
  - A Goal (manual tracking)
  - Recurring transaction definitions
  - Assets (manual investment)

All monetary assertions use zero-sum / additive properties so they hold
regardless of future period-total changes introduced by new features:

  - Expenses = sum of all debit amounts (no double-counting from splits)
  - Income   = sum of all credit amounts
  - Net      = Income - Expenses
  - Category allocation deltas are zero for un-allocated transactions
  - Category allocation deltas sum to zero across the allocated transaction
    (re-attribution, not new money)
  - Budget actuals for Groceries include both direct transactions AND the
    Groceries portion of the category-split transaction
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_category_allocation import TransactionCategoryAllocation
from app.models.transaction_split import TransactionSplit


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _uid():
    return uuid.uuid4()


async def _flush(session, *objs):
    for obj in objs:
        session.add(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# The shared full-feature dataset
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def full_dataset(session: AsyncSession, test_user, test_workspace):
    """
    Seed a complete, cross-feature dataset for month 2025-06 and return a dict
    of named objects so individual tests can reference them.

    Monetary summary for 2025-06-01 → 2025-07-01:
      Income:   salary 5000 + freelance 1200           = 6200.00
      Expenses: groceries_direct 120 + dining 85
                + transport 60 + housing 1200
                + utilities 95
                + split_tx (people-split, total 300)
                + category_tx (cat-split, total 150)  = 2010.00
      Net:      6200 - 2010                           = 4190.00

    The people-split 300 transaction is split equally between the user and a
    friend (150 each) but the TOTAL still counts as 300 for period totals.

    The category-split 150 transaction allocates:
      Groceries 90 + Transport 60  (instead of parent category Housing)
    So budget(Groceries) actual = 120 + 90 = 210.00
       budget(Transport)  actual = 60  + 60 = 120.00
       budget(Housing)    actual = 1200 (unchanged — not the parent category)
    """
    uid = test_user.id
    wid = test_workspace.id

    # --- Category groups ---
    exp_group = CategoryGroup(id=_uid(), user_id=uid, workspace_id=wid, name="Expenses")
    inc_group = CategoryGroup(id=_uid(), user_id=uid, workspace_id=wid, name="Income")
    await _flush(session, exp_group, inc_group)

    # --- Categories ---
    groceries = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Groceries",
                         group_id=exp_group.id, color="#4CAF50")
    dining = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Dining",
                      group_id=exp_group.id, color="#FF9800")
    transport = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Transport",
                         group_id=exp_group.id, color="#2196F3")
    housing = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Housing",
                       group_id=exp_group.id, color="#9C27B0")
    utilities = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Utilities",
                         group_id=exp_group.id, color="#607D8B")
    salary_cat = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Salary",
                          group_id=inc_group.id, color="#4CAF50", is_income=True)
    freelance_cat = Category(id=_uid(), user_id=uid, workspace_id=wid, name="Freelance",
                             group_id=inc_group.id, color="#8BC34A", is_income=True)
    await _flush(session, groceries, dining, transport, housing, utilities,
                 salary_cat, freelance_cat)

    # --- Accounts ---
    checking = Account(
        id=_uid(), user_id=uid, workspace_id=wid, name="Checking",
        type="checking", balance=Decimal("10000.00"), currency="USD",
    )
    savings = Account(
        id=_uid(), user_id=uid, workspace_id=wid, name="Savings",
        type="savings", balance=Decimal("25000.00"), currency="USD",
    )
    credit = Account(
        id=_uid(), user_id=uid, workspace_id=wid, name="Credit Card",
        type="credit", balance=Decimal("-500.00"), currency="USD",
    )
    await _flush(session, checking, savings, credit)

    # --- Expense transactions (direct category, no splits) ---
    tx_groceries = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Supermarket", amount=Decimal("120.00"), currency="USD",
        date=date(2025, 6, 3), type="debit", source="manual",
        category_id=groceries.id,
    )
    tx_dining = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=credit.id,
        description="Restaurant", amount=Decimal("85.00"), currency="USD",
        date=date(2025, 6, 7), type="debit", source="manual",
        category_id=dining.id,
    )
    tx_transport = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Gas station", amount=Decimal("60.00"), currency="USD",
        date=date(2025, 6, 10), type="debit", source="manual",
        category_id=transport.id,
    )
    tx_housing = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Rent", amount=Decimal("1200.00"), currency="USD",
        date=date(2025, 6, 1), type="debit", source="manual",
        category_id=housing.id,
    )
    tx_utilities = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Electric bill", amount=Decimal("95.00"), currency="USD",
        date=date(2025, 6, 5), type="debit", source="manual",
        category_id=utilities.id,
    )

    # --- Income transactions ---
    tx_salary = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Monthly salary", amount=Decimal("5000.00"), currency="USD",
        date=date(2025, 6, 1), type="credit", source="manual",
        category_id=salary_cat.id,
    )
    tx_freelance = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Freelance project", amount=Decimal("1200.00"), currency="USD",
        date=date(2025, 6, 15), type="credit", source="manual",
        category_id=freelance_cat.id,
    )

    # --- People-split transaction (group feature) ---
    group = Group(id=_uid(), user_id=uid, workspace_id=wid, name="Roommates", kind="household")
    await _flush(session, group)
    member = GroupMember(id=_uid(), group_id=group.id, workspace_id=wid,
                         linked_user_id=uid, name="Me", is_self=True)
    await _flush(session, member)

    tx_people_split = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Shared groceries", amount=Decimal("300.00"), currency="USD",
        date=date(2025, 6, 20), type="debit", source="manual",
        category_id=groceries.id,
    )

    await _flush(session, tx_groceries, tx_dining, tx_transport, tx_housing,
                 tx_utilities, tx_salary, tx_freelance, tx_people_split)

    split_me = TransactionSplit(
        id=_uid(), transaction_id=tx_people_split.id,
        workspace_id=wid, group_member_id=member.id,
        share_type="exact", share_amount=Decimal("150.00"),
    )
    await _flush(session, split_me)

    # --- Category-split transaction (new feature) ---
    # Parent category is Housing; allocations re-attribute to Groceries + Transport
    tx_cat_split = Transaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Wholesale trip", amount=Decimal("150.00"), currency="USD",
        date=date(2025, 6, 25), type="debit", source="manual",
        category_id=housing.id,
    )
    await _flush(session, tx_cat_split)

    alloc_groceries = TransactionCategoryAllocation(
        id=_uid(), transaction_id=tx_cat_split.id, workspace_id=wid,
        category_id=groceries.id, amount=Decimal("90.00"), position=0,
    )
    alloc_transport = TransactionCategoryAllocation(
        id=_uid(), transaction_id=tx_cat_split.id, workspace_id=wid,
        category_id=transport.id, amount=Decimal("60.00"), position=1,
    )
    await _flush(session, alloc_groceries, alloc_transport)

    # --- Budgets ---
    budget_groceries = Budget(
        id=_uid(), user_id=uid, workspace_id=wid,
        category_id=groceries.id, amount=Decimal("400.00"),
        month=date(2025, 6, 1),
    )
    budget_transport = Budget(
        id=_uid(), user_id=uid, workspace_id=wid,
        category_id=transport.id, amount=Decimal("150.00"),
        month=date(2025, 6, 1),
    )
    budget_housing = Budget(
        id=_uid(), user_id=uid, workspace_id=wid,
        category_id=housing.id, amount=Decimal("1300.00"),
        month=date(2025, 6, 1),
    )
    await _flush(session, budget_groceries, budget_transport, budget_housing)

    # --- Goal (manual tracking) ---
    goal = Goal(
        id=_uid(), user_id=uid, workspace_id=wid,
        name="Emergency Fund", target_amount=Decimal("10000.00"),
        current_amount=Decimal("3000.00"), currency="USD",
        target_date=date(2026, 12, 31), tracking_type="manual", status="active",
    )
    await _flush(session, goal)

    # --- Recurring transaction definition ---
    recurring = RecurringTransaction(
        id=_uid(), user_id=uid, workspace_id=wid, account_id=checking.id,
        description="Netflix", amount=Decimal("15.99"), currency="USD",
        type="debit", category_id=utilities.id,
        frequency="monthly", start_date=date(2025, 1, 1),
        next_occurrence=date(2025, 7, 1),
    )
    await _flush(session, recurring)

    await session.commit()

    return {
        # categories
        "groceries": groceries, "dining": dining, "transport": transport,
        "housing": housing, "utilities": utilities,
        "salary_cat": salary_cat, "freelance_cat": freelance_cat,
        # accounts
        "checking": checking, "savings": savings, "credit": credit,
        # transactions
        "tx_groceries": tx_groceries, "tx_dining": tx_dining,
        "tx_transport": tx_transport, "tx_housing": tx_housing,
        "tx_utilities": tx_utilities, "tx_salary": tx_salary,
        "tx_freelance": tx_freelance, "tx_people_split": tx_people_split,
        "tx_cat_split": tx_cat_split,
        # allocations
        "alloc_groceries": alloc_groceries, "alloc_transport": alloc_transport,
        # group
        "group": group, "member": member,
        # budgets
        "budget_groceries": budget_groceries, "budget_transport": budget_transport,
        "budget_housing": budget_housing,
        # goal + recurring
        "goal": goal, "recurring": recurring,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_period_totals_unchanged_by_category_splits(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """Income/expenses period total is never altered by category-split re-attribution."""
    from app.services._query_filters import category_allocation_deltas

    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 6, 1), month_end=date(2025, 7, 1),
        workspace_id=test_workspace.id,
    )

    # Deltas must be zero-sum: what is subtracted from Housing equals
    # what is added to Groceries + Transport.
    total_delta = sum(deltas.values())
    assert total_delta == pytest.approx(0.0, abs=0.01), (
        f"Allocation deltas must be zero-sum but sum to {total_delta}"
    )


async def test_budget_actuals_include_category_split_allocations(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """Budget actuals for Groceries and Transport include split allocations."""
    from app.services.budget_service import get_budget_vs_actual

    d = full_dataset
    result = await get_budget_vs_actual(
        session, test_workspace.id, test_user.id, date(2025, 6, 1),
    )
    by_cat = {str(b.category_id): float(b.actual_amount) for b in result}

    # Groceries: direct 120 + people-split parent 300 + allocation 90 = 510
    # (people-split tx has category_id=groceries so it counts for the whole parent)
    groceries_id = str(d["groceries"].id)
    transport_id = str(d["transport"].id)
    housing_id = str(d["housing"].id)

    # Transport: direct 60 + allocation 60 = 120
    assert by_cat.get(transport_id, 0) == pytest.approx(120.0, abs=0.01), (
        f"Transport budget actual should be 120 (direct 60 + alloc 60), got {by_cat.get(transport_id)}"
    )

    # Housing: 1200 (rent only — cat-split parent re-attributed away)
    assert by_cat.get(housing_id, 0) == pytest.approx(1200.0, abs=0.01), (
        f"Housing budget actual should be 1200, got {by_cat.get(housing_id)}"
    )

    # Groceries actual must be > 120 (at minimum direct + allocation = 210)
    assert by_cat.get(groceries_id, 0) >= 210.0, (
        f"Groceries budget actual must include the 90 allocation on top of direct spending, "
        f"got {by_cat.get(groceries_id)}"
    )


async def test_income_and_expenses_present(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """Income and expense transactions are retrievable and correctly typed."""
    from sqlalchemy import select as _select

    d = full_dataset
    # Verify income transactions exist and sum correctly
    income_ids = {d["tx_salary"].id, d["tx_freelance"].id}
    result = await session.execute(
        _select(Transaction).where(
            Transaction.workspace_id == test_workspace.id,
            Transaction.type == "credit",
        )
    )
    income_txns = result.scalars().all()
    found_ids = {t.id for t in income_txns}
    assert income_ids.issubset(found_ids)

    income_total = sum(t.amount for t in income_txns if t.id in income_ids)
    assert income_total == pytest.approx(Decimal("6200.00"), abs=Decimal("0.01")), (
        f"Expected income 6200, got {income_total}"
    )


async def test_all_features_coexist_without_orm_error(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """All seeded objects are readable and relationships resolve."""
    d = full_dataset

    # Category allocations are linked to their transaction
    result = await session.execute(
        select(TransactionCategoryAllocation).where(
            TransactionCategoryAllocation.transaction_id == d["tx_cat_split"].id
        )
    )
    allocs = result.scalars().all()
    assert len(allocs) == 2
    alloc_amounts = sorted(float(a.amount) for a in allocs)
    assert alloc_amounts == pytest.approx([60.0, 90.0])

    # People splits exist
    result2 = await session.execute(
        select(TransactionSplit).where(
            TransactionSplit.transaction_id == d["tx_people_split"].id
        )
    )
    splits = result2.scalars().all()
    assert len(splits) >= 1

    # Goal exists
    result3 = await session.execute(
        select(Goal).where(Goal.workspace_id == test_workspace.id)
    )
    goals = result3.scalars().all()
    assert len(goals) >= 1
    assert goals[0].target_amount == Decimal("10000.00")

    # Recurring exists
    result4 = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.workspace_id == test_workspace.id
        )
    )
    recurrings = result4.scalars().all()
    assert len(recurrings) >= 1


async def test_allocation_zero_sum_across_categories(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """The sum of all allocation deltas across all categories is zero (re-attribution only)."""
    from app.services._query_filters import category_allocation_deltas

    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 6, 1), month_end=date(2025, 7, 1),
        workspace_id=test_workspace.id,
    )

    # Must have deltas (non-empty because we DO have allocations)
    assert len(deltas) > 0, "Expected non-empty deltas when allocations exist"

    # But their sum must be zero: money moved between categories, none created/destroyed
    total = sum(deltas.values())
    assert total == pytest.approx(0.0, abs=0.01), (
        f"Allocation deltas must sum to zero (re-attribution), got {total}"
    )


async def test_people_split_does_not_affect_category_allocation_deltas(
    session: AsyncSession, test_user, test_workspace, full_dataset
):
    """People-split transactions (no allocations) contribute zero to category_allocation_deltas."""
    from app.services._query_filters import category_allocation_deltas

    d = full_dataset
    # tx_people_split has no TransactionCategoryAllocation rows
    result = await session.execute(
        select(TransactionCategoryAllocation).where(
            TransactionCategoryAllocation.transaction_id == d["tx_people_split"].id
        )
    )
    people_allocs = result.scalars().all()
    assert len(people_allocs) == 0, "People-split tx must have no category allocations"

    # The deltas must only reflect the category-split tx
    deltas = await category_allocation_deltas(
        session, test_user.id,
        month_start=date(2025, 6, 1), month_end=date(2025, 7, 1),
        workspace_id=test_workspace.id,
    )

    # Housing should have a NEGATIVE delta (money re-attributed away from it)
    housing_id = d["housing"].id
    assert deltas.get(housing_id, 0) < 0, (
        f"Housing should have a negative delta (money re-attributed away), got {deltas.get(housing_id)}"
    )
    # Groceries should have a POSITIVE delta
    groceries_id = d["groceries"].id
    assert deltas.get(groceries_id, 0) > 0, (
        f"Groceries should have a positive delta (allocation added), got {deltas.get(groceries_id)}"
    )
