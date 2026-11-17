"""Tests for budget suggestions (issue #430).

Suggestions average the net PnL spending of the N calendar months
immediately before the target month, excluding any month that is not fully
in the past. Batch creation writes several budgets atomically.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.budget import Budget
from app.models.transaction import Transaction
from app.schemas.budget import (
    BudgetBatchCreate,
    BudgetBatchItem,
    BudgetCreate,
)
from app.services.budget_service import (
    _add_months,
    create_budgets_batch,
    get_budget_suggestions,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_account(session: AsyncSession, test_user, name: str) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name=name,
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


def _txn(test_user, account, category, amount, day, kind="debit", pair_id=None):
    return Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=category.id,
        description="SUGGEST TEST",
        amount=Decimal(str(amount)),
        date=day,
        type=kind,
        source="manual",
        transfer_pair_id=pair_id,
        created_at=datetime.now(timezone.utc),
    )


async def _suggest(session, test_user, test_workspace, month, months=3):
    return await get_budget_suggestions(
        session, test_workspace.id, test_user.id, month, months
    )


def _by_cat(response, cat_id):
    return [s for s in response.suggestions if s.category_id == cat_id]


# ---------------------------------------------------------------------------
# _add_months
# ---------------------------------------------------------------------------


def test_add_months_wraps_years():
    assert _add_months(date(2025, 3, 10), -2) == date(2025, 1, 1)
    assert _add_months(date(2025, 1, 15), -1) == date(2024, 12, 1)
    assert _add_months(date(2024, 12, 1), 2) == date(2025, 2, 1)


# ---------------------------------------------------------------------------
# get_budget_suggestions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_months_with_data_nets_the_whole_month(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A debit and an equal refund in the same month leave no data for it."""
    account = await _make_account(session, test_user, "SugAccMonthNet")
    cat_food = test_categories[0]
    session.add_all([
        _txn(test_user, account, cat_food, 100, date(2025, 4, 1)),
        _txn(test_user, account, cat_food, 100, date(2025, 4, 5), kind="credit"),
        _txn(test_user, account, cat_food, 300, date(2025, 5, 8)),
    ])
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)

    food = _by_cat(resp, cat_food.id)
    assert len(food) == 1
    assert food[0].total_spent == Decimal("300")
    assert food[0].months_with_data == 1


@pytest.mark.asyncio
async def test_suggestions_average_over_window(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = await _make_account(session, test_user, "SugAcc1")
    cat_food, cat_transit, _ = test_categories

    txns = [
        # food: 100 + 200 + 300 over the 3 window months -> 600 / 3 = 200.00
        _txn(test_user, account, cat_food, 100, date(2025, 3, 10)),
        _txn(test_user, account, cat_food, 200, date(2025, 4, 12)),
        _txn(test_user, account, cat_food, 300, date(2025, 5, 8)),
        # transit: single month of spend -> 150 / 3 = 50.00
        _txn(test_user, account, cat_transit, 150, date(2025, 4, 5)),
    ]
    session.add_all(txns)
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)

    assert resp.month == date(2025, 6, 1)
    assert resp.months_requested == 3
    assert resp.months_analyzed == 3

    food = _by_cat(resp, cat_food.id)
    assert len(food) == 1
    assert food[0].suggested_amount == Decimal("200.00")
    assert food[0].total_spent == Decimal("600")
    assert food[0].months_with_data == 3

    transit = _by_cat(resp, cat_transit.id)
    assert len(transit) == 1
    assert transit[0].suggested_amount == Decimal("50.00")
    assert transit[0].months_with_data == 1

    # Highest suggested amount first
    assert resp.suggestions[0].category_id == cat_food.id


@pytest.mark.asyncio
async def test_suggestions_credit_income_not_suggested(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = await _make_account(session, test_user, "SugAcc2")
    cat_income = test_categories[2]

    session.add(_txn(test_user, account, cat_income, 5000, date(2025, 4, 30), kind="credit"))
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)
    assert _by_cat(resp, cat_income.id) == []


@pytest.mark.asyncio
async def test_suggestions_refunds_reduce_average(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = await _make_account(session, test_user, "SugAcc3")
    cat_food = test_categories[0]

    txns = [
        _txn(test_user, account, cat_food, 200, date(2025, 3, 10)),
        _txn(test_user, account, cat_food, 50, date(2025, 4, 10), kind="credit"),
    ]
    session.add_all(txns)
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)
    food = _by_cat(resp, cat_food.id)
    assert len(food) == 1
    assert food[0].total_spent == Decimal("150")
    assert food[0].suggested_amount == Decimal("50.00")


@pytest.mark.asyncio
async def test_suggestions_excludes_transfers(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = await _make_account(session, test_user, "SugAcc4")
    cat_transit = test_categories[1]
    pair_id = uuid.uuid4()

    txns = [
        _txn(test_user, account, cat_transit, 200, date(2025, 4, 5), pair_id=pair_id),
        _txn(test_user, account, cat_transit, 200, date(2025, 4, 5), kind="credit", pair_id=pair_id),
    ]
    session.add_all(txns)
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)
    assert resp.months_analyzed == 3
    assert resp.suggestions == []


@pytest.mark.asyncio
async def test_suggestions_excludes_partial_current_month(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    today = app_today()
    current_month = today.replace(day=1)
    target = _add_months(current_month, 1)  # next month
    cat_food = test_categories[0]
    account = await _make_account(session, test_user, "SugAcc5")

    txns = [
        # Inside the window: current month minus 2
        _txn(test_user, account, cat_food, 100, _add_months(current_month, -2).replace(day=15)),
        # The current month is still running and must not skew the average
        _txn(test_user, account, cat_food, 300, today),
    ]
    session.add_all(txns)
    await session.commit()

    resp = await _suggest(session, test_user, test_workspace, target, months=3)
    assert resp.months_requested == 3
    assert resp.months_analyzed == 2

    food = _by_cat(resp, cat_food.id)
    assert len(food) == 1
    assert food[0].total_spent == Decimal("100")
    assert food[0].suggested_amount == Decimal("50.00")
    assert food[0].months_with_data == 1


@pytest.mark.asyncio
async def test_suggestions_marks_existing_budget(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    today = app_today()
    target = today.replace(day=1)
    cat_food, cat_transit, _ = test_categories
    account = await _make_account(session, test_user, "SugAcc6")

    txns = [
        _txn(test_user, account, cat_food, 100, _add_months(target, -2).replace(day=10)),
        _txn(test_user, account, cat_food, 200, _add_months(target, -1).replace(day=10)),
        _txn(test_user, account, cat_transit, 90, _add_months(target, -3).replace(day=10)),
    ]
    session.add_all(txns)
    await session.commit()

    from app.services.budget_service import create_budget

    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=cat_food.id, amount=Decimal("150"), month=target),
    )

    resp = await _suggest(session, test_user, test_workspace, target, months=3)

    food = _by_cat(resp, cat_food.id)
    assert len(food) == 1
    assert food[0].suggested_amount == Decimal("100.00")
    assert food[0].existing_budget is True

    transit = _by_cat(resp, cat_transit.id)
    assert len(transit) == 1
    assert transit[0].existing_budget is False


@pytest.mark.asyncio
async def test_suggestions_empty_without_history(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    resp = await _suggest(session, test_user, test_workspace, date(2025, 6, 1), months=3)
    assert resp.months_analyzed == 3
    assert resp.suggestions == []


@pytest.mark.asyncio
async def test_suggestions_window_entirely_in_future(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    today = app_today()
    target = _add_months(today.replace(day=1), 6)
    resp = await _suggest(session, test_user, test_workspace, target, months=1)
    assert resp.months_analyzed == 0
    assert resp.suggestions == []


# ---------------------------------------------------------------------------
# create_budgets_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_creates_multiple(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    data = BudgetBatchCreate(
        month=date(2026, 11, 15),  # mid-month should normalize to day 1
        items=[
            BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("100.50")),
            BudgetBatchItem(category_id=test_categories[1].id, amount=Decimal("200")),
        ],
    )
    budgets = await create_budgets_batch(session, test_workspace.id, test_user.id, data)

    assert len(budgets) == 2
    assert all(b.month == date(2026, 11, 1) for b in budgets)
    assert all(b.is_recurring is False for b in budgets)


@pytest.mark.asyncio
async def test_batch_recurring_flag(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    data = BudgetBatchCreate(
        month=date(2026, 11, 1),
        is_recurring=True,
        items=[BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("50"))],
    )
    budgets = await create_budgets_batch(session, test_workspace.id, test_user.id, data)
    assert budgets[0].is_recurring is True


@pytest.mark.asyncio
async def test_batch_dedupes_repeated_category(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    data = BudgetBatchCreate(
        month=date(2026, 11, 1),
        items=[
            BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("100")),
            BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("200")),
        ],
    )
    budgets = await create_budgets_batch(session, test_workspace.id, test_user.id, data)
    assert len(budgets) == 1


def test_batch_item_rejects_non_positive_amount():
    with pytest.raises(ValidationError):
        BudgetBatchItem(category_id=uuid.uuid4(), amount=Decimal("0"))
    with pytest.raises(ValidationError):
        BudgetBatchItem(category_id=uuid.uuid4(), amount=Decimal("-5"))


def test_batch_rejects_empty_items():
    with pytest.raises(ValidationError):
        BudgetBatchCreate(month=date(2026, 11, 1), items=[])


@pytest.mark.asyncio
async def test_batch_atomic_on_conflict(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    from app.services.budget_service import create_budget

    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=test_categories[0].id, amount=Decimal("1"), month=date(2026, 11, 1)),
    )

    data = BudgetBatchCreate(
        month=date(2026, 11, 1),
        items=[
            BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("100")),
            BudgetBatchItem(category_id=test_categories[1].id, amount=Decimal("200")),
        ],
    )
    with pytest.raises(ValueError):
        await create_budgets_batch(session, test_workspace.id, test_user.id, data)

    # Nothing was written: only the pre-existing budget remains for that month
    count = (
        await session.execute(
            select(func.count(Budget.id)).where(Budget.month == date(2026, 11, 1))
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_batch_rejects_category_outside_workspace(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    data = BudgetBatchCreate(
        month=date(2026, 11, 1),
        items=[
            BudgetBatchItem(category_id=test_categories[0].id, amount=Decimal("100")),
            BudgetBatchItem(category_id=uuid.uuid4(), amount=Decimal("200")),
        ],
    )
    with pytest.raises(ValueError, match="Category not found"):
        await create_budgets_batch(session, test_workspace.id, test_user.id, data)

    count = (
        await session.execute(
            select(func.count(Budget.id)).where(Budget.month == date(2026, 11, 1))
        )
    ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_suggestions(client, auth_headers, session, test_user, test_workspace, test_categories):
    account = await _make_account(session, test_user, "SugApiAcc")
    cat_food = test_categories[0]
    session.add(_txn(test_user, account, cat_food, 300, date(2025, 5, 20)))
    await session.commit()

    response = await client.get(
        "/api/budgets/suggestions?month=2025-06-01&months=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["month"] == "2025-06-01"
    assert body["months_analyzed"] == 3
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["suggested_amount"] == "100.00"
    assert body["suggestions"][0]["existing_budget"] is False


@pytest.mark.asyncio
async def test_api_batch_creates(client, auth_headers, test_categories):
    response = await client.post(
        "/api/budgets/batch",
        json={
            "month": "2026-12-10",
            "items": [
                {"category_id": str(test_categories[0].id), "amount": "100.00"},
                {"category_id": str(test_categories[1].id), "amount": "75"},
            ],
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert len(data) == 2
    assert all(b["month"] == "2026-12-01" for b in data)

    listing = await client.get("/api/budgets?month=2026-12-01", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_api_batch_conflict(client, auth_headers, test_categories):
    existing = await client.post(
        "/api/budgets",
        json={
            "category_id": str(test_categories[0].id),
            "amount": 1,
            "month": "2026-12-01",
        },
        headers=auth_headers,
    )
    assert existing.status_code == 201

    response = await client.post(
        "/api/budgets/batch",
        json={
            "month": "2026-12-01",
            "items": [
                {"category_id": str(test_categories[0].id), "amount": "100.00"},
                {"category_id": str(test_categories[1].id), "amount": "200"},
            ],
        },
        headers=auth_headers,
    )
    assert response.status_code == 400

    listing = await client.get("/api/budgets?month=2026-12-01", headers=auth_headers)
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_api_batch_rejects_bad_amount(client, auth_headers, test_categories):
    response = await client.post(
        "/api/budgets/batch",
        json={
            "month": "2026-12-01",
            "items": [{"category_id": str(test_categories[0].id), "amount": 0}],
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
