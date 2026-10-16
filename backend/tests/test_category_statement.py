import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.services.category_statement_service import (
    _shift_month,
    get_category_statement,
)


def test_shift_month_crosses_year_boundaries():
    assert _shift_month(date(2025, 1, 1), -1) == date(2024, 12, 1)
    assert _shift_month(date(2025, 12, 1), 1) == date(2026, 1, 1)
    assert _shift_month(date(2025, 3, 1), -14) == date(2024, 1, 1)


async def _account(session: AsyncSession, user_id, name="Statement", closed=False) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
        is_closed=closed,
    )
    session.add(account)
    await session.commit()
    return account


def _txn(user_id, account, amount, day, typ, category_id=None, **extra) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account.id,
        category_id=category_id,
        description="TXN",
        amount=Decimal(amount),
        date=day,
        type=typ,
        source="manual",
        created_at=datetime.now(timezone.utc),
        **extra,
    )


@pytest.fixture
async def statement_data(session: AsyncSession, test_user, test_categories):
    """Food sits in a group, Transport and Salary stay ungrouped.

    Feb 2025: salary 5000, food 300 with a 50 refund, transport 100,
    an uncategorized 20 credit and 40 debit.
    Jan 2025: salary 4000, food 200.
    """
    food, transport, salary = test_categories
    group = CategoryGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Living",
        icon="home",
        color="#111111",
        position=0,
    )
    session.add(group)
    await session.commit()
    food.group_id = group.id
    await session.commit()

    account = await _account(session, test_user.id)
    session.add_all([
        _txn(test_user.id, account, "5000", date(2025, 2, 5), "credit", salary.id),
        _txn(test_user.id, account, "300", date(2025, 2, 10), "debit", food.id),
        _txn(test_user.id, account, "50", date(2025, 2, 12), "credit", food.id),
        _txn(test_user.id, account, "100", date(2025, 2, 15), "debit", transport.id),
        _txn(test_user.id, account, "20", date(2025, 2, 16), "credit"),
        _txn(test_user.id, account, "40", date(2025, 2, 17), "debit"),
        _txn(test_user.id, account, "4000", date(2025, 1, 5), "credit", salary.id),
        _txn(test_user.id, account, "200", date(2025, 1, 10), "debit", food.id),
    ])
    await session.commit()
    return {"group": group, "account": account, "categories": test_categories}


@pytest.mark.asyncio
async def test_statement_nets_categories_into_sections(
    session: AsyncSession, test_user, test_workspace, statement_data
):
    food, transport, salary = statement_data["categories"]
    group = statement_data["group"]

    result = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=2,
        primary_currency="BRL",
    )

    assert result.months == ["2025-02", "2025-01"]
    assert result.currency == "BRL"

    income = {row.key: row for row in result.income.rows}
    assert income[str(salary.id)].values == [5000.0, 4000.0]
    assert income["uncategorized"].values == [20.0, 0.0]
    assert income["uncategorized"].txn_type == "credit"
    assert result.income.totals == [5020.0, 4000.0]

    expenses = {row.key: row for row in result.expenses.rows}
    living = expenses[str(group.id)]
    assert living.kind == "group"
    assert living.label == "Living"
    # The refund lowers the food spending instead of counting as income.
    assert living.values == [-250.0, -200.0]
    assert [child.key for child in living.children] == [str(food.id)]
    assert living.category_ids == [str(food.id)]
    assert expenses[str(transport.id)].values == [-100.0, 0.0]
    assert expenses["uncategorized"].values == [-40.0, 0.0]
    assert expenses["uncategorized"].txn_type == "debit"
    assert result.expenses.totals == [-390.0, -200.0]

    # Groups come first, ungrouped categories next, uncategorized last.
    assert [row.key for row in result.expenses.rows] == [
        str(group.id), str(transport.id), "uncategorized",
    ]


@pytest.mark.asyncio
async def test_statement_skips_rows_without_activity_in_window(
    session: AsyncSession, test_user, test_workspace, statement_data
):
    _, transport, _ = statement_data["categories"]

    result = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 1, 1), months=1,
        primary_currency="BRL",
    )

    assert result.months == ["2025-01"]
    expense_keys = {row.key for row in result.expenses.rows}
    assert str(transport.id) not in expense_keys
    assert "uncategorized" not in expense_keys
    assert result.expenses.totals == [-200.0]


@pytest.mark.asyncio
async def test_statement_respects_account_filter_and_closed_accounts(
    session: AsyncSession, test_user, test_workspace, statement_data
):
    food, _, _ = statement_data["categories"]
    other = await _account(session, test_user.id, name="Other")
    closed = await _account(session, test_user.id, name="Closed", closed=True)
    session.add_all([
        _txn(test_user.id, other, "70", date(2025, 2, 20), "debit", food.id),
        _txn(test_user.id, closed, "999", date(2025, 2, 21), "debit", food.id),
    ])
    await session.commit()

    everything = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    assert everything.expenses.rows[0].values == [-320.0]

    scoped = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL", account_ids=[other.id],
    )
    assert [row.values for row in scoped.expenses.rows] == [[-70.0]]
    assert scoped.income.rows == []


@pytest.mark.asyncio
async def test_statement_leaves_out_ignored_and_transfers(
    session: AsyncSession, test_user, test_workspace, statement_data
):
    food, _, _ = statement_data["categories"]
    account = statement_data["account"]
    session.add_all([
        _txn(test_user.id, account, "500", date(2025, 2, 22), "debit", food.id, is_ignored=True),
        _txn(
            test_user.id, account, "800", date(2025, 2, 23), "debit", food.id,
            transfer_pair_id=uuid.uuid4(),
        ),
        _txn(test_user.id, account, "60", date(2025, 2, 24), "debit", food.id, status="pending"),
    ])
    await session.commit()

    result = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    assert result.expenses.rows[0].values == [-250.0]


@pytest.mark.asyncio
async def test_statement_places_rows_by_lifetime_net(
    session: AsyncSession, test_user, test_workspace, statement_data
):
    """A month where salary nets negative keeps it in the income section."""
    _, _, salary = statement_data["categories"]
    account = statement_data["account"]
    session.add(_txn(test_user.id, account, "30", date(2025, 3, 3), "debit", salary.id))
    await session.commit()

    result = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 3, 1), months=1,
        primary_currency="BRL",
    )
    income = {row.key: row for row in result.income.rows}
    assert income[str(salary.id)].values == [-30.0]
    assert str(salary.id) not in {row.key for row in result.expenses.rows}


@pytest.mark.asyncio
async def test_category_statement_endpoint(client, auth_headers, statement_data):
    response = await client.get(
        "/api/reports/category-statement",
        params={"month": "2025-02", "months": 2},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["months"] == ["2025-02", "2025-01"]
    assert body["income"]["totals"] == [5020.0, 4000.0]
    assert body["expenses"]["totals"] == [-390.0, -200.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"month": "2025-13"}, {"months": 0}, {"months": 13}])
async def test_category_statement_endpoint_validates_params(client, auth_headers, params):
    response = await client.get(
        "/api/reports/category-statement", params=params, headers=auth_headers,
    )
    assert response.status_code == 422
