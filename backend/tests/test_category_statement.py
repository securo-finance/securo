import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.fx_rate import FxRate
from app.models.group import Group, GroupMember
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.models.transaction import Transaction
from app.core.app_clock import app_today
from app.services.category_statement_service import (
    _shift_month,
    get_category_statement,
)
from app.services.workspace_service import create_workspace


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
async def test_statement_respects_account_filter_and_keeps_closed_accounts(
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
    # A closed account keeps its history, as in the other period reports.
    assert everything.expenses.rows[0].values == [-1319.0]

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


async def _workspace_member(session: AsyncSession, workspace_id, email: str) -> User:
    user = User(
        id=uuid.uuid4(), email=email, hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace_id, user_id=user.id, role="member"))
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_statement_counts_each_members_share_of_a_split(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A 100 expense split 50/50 between its owner and a workspace member
    reads -50 for both; a member outside the split sees the full amount, and
    an account filter drops the split rules for everyone."""
    food = test_categories[0]
    member = await _workspace_member(session, test_workspace.id, "member@example.com")
    bystander = await _workspace_member(session, test_workspace.id, "bystander@example.com")

    group = Group(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Home")
    session.add(group)
    await session.flush()
    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Me", is_self=True)
    friend = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
        name="Member", linked_user_id=member.id,
    )
    session.add_all([me, friend])
    account = await _account(session, test_user.id)
    tx = _txn(test_user.id, account, "100", date(2025, 2, 10), "debit", food.id)
    session.add(tx)
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=me.id, share_amount=Decimal("50")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=friend.id, share_amount=Decimal("50")),
    ])
    await session.commit()

    async def food_value(user_id, **kwargs):
        statement = await get_category_statement(
            session, test_workspace.id, user_id, date(2025, 2, 1), months=1,
            primary_currency="BRL", **kwargs,
        )
        return statement.expenses.rows[0].values

    assert await food_value(test_user.id) == [-50.0]
    assert await food_value(member.id) == [-50.0]
    assert await food_value(bystander.id) == [-100.0]
    assert await food_value(test_user.id, account_ids=[account.id]) == [-100.0]
    assert await food_value(member.id, account_ids=[account.id]) == [-100.0]


@pytest.mark.asyncio
async def test_statement_places_rows_by_the_users_share(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A 100 refund against a 150 expense of which the user owes 50 nets
    +50 for the user, so the row belongs in income even though the full
    amounts net to -50."""
    food = test_categories[0]
    group = Group(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Trip")
    session.add(group)
    await session.flush()
    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Me", is_self=True)
    friend = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Friend")
    session.add_all([me, friend])
    account = await _account(session, test_user.id)
    expense = _txn(test_user.id, account, "150", date(2025, 2, 10), "debit", food.id)
    session.add_all([expense, _txn(test_user.id, account, "100", date(2025, 2, 12), "credit", food.id)])
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=expense.id, workspace_id=test_workspace.id,
                         group_member_id=me.id, share_amount=Decimal("50")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=expense.id, workspace_id=test_workspace.id,
                         group_member_id=friend.id, share_amount=Decimal("100")),
    ])
    await session.commit()

    statement = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    assert [row.values for row in statement.income.rows] == [[50.0]]
    assert statement.expenses.rows == []


@pytest.mark.asyncio
async def test_uncategorized_drill_down_lists_shares_from_other_workspaces(
    session: AsyncSession, client, auth_headers, test_user, test_workspace
):
    """Someone else's 100 expense in their own workspace, split 50 with the
    user, carries a category of that workspace. The statement counts the
    user's 50 as uncategorized, and the uncategorized drill-down lists the
    row so the panel adds up to the cell."""
    owner = User(
        id=uuid.uuid4(), email="owner@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(owner)
    await session.flush()
    other_ws = await create_workspace(
        session, name="Owner's", creator=owner, self_membership=True, seed_defaults=False,
    )
    dining = Category(id=uuid.uuid4(), user_id=owner.id, workspace_id=other_ws.id, name="Dining")
    account = Account(
        id=uuid.uuid4(), user_id=owner.id, workspace_id=other_ws.id, name="Owner's",
        type="checking", balance=Decimal("0"), currency="BRL",
    )
    group = Group(id=uuid.uuid4(), user_id=owner.id, workspace_id=other_ws.id, name="Dinners")
    session.add_all([dining, account, group])
    await session.flush()
    owner_member = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=other_ws.id, name="Owner", is_self=True,
    )
    user_member = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=other_ws.id,
        name="Friend", linked_user_id=test_user.id,
    )
    session.add_all([owner_member, user_member])
    tx = _txn(owner.id, account, "100", date(2025, 2, 10), "debit", dining.id, workspace_id=other_ws.id)
    session.add(tx)
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=other_ws.id,
                         group_member_id=owner_member.id, share_amount=Decimal("50")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=other_ws.id,
                         group_member_id=user_member.id, share_amount=Decimal("50")),
    ])
    await session.commit()

    statement = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    [row] = statement.expenses.rows
    assert (row.kind, row.values) == ("uncategorized", [-50.0])

    params = {
        "uncategorized": "true", "type": "debit", "status": "posted", "user_pnl_only": "true",
        "from": "2025-02-01", "to": "2025-02-28",
    }
    resp = await client.get(
        "/api/transactions", headers=auth_headers,
        params={**params, "shared_as_uncategorized": "true"},
    )
    assert resp.status_code == 200
    [item] = resp.json()["items"]
    assert item["id"] == str(tx.id)
    assert item["is_shared"] is True
    assert float(item["viewer_share"]) == 50.0

    # The plain uncategorized filter, used to categorize rows, keeps
    # leaving it out: the user can't recategorize someone else's row.
    resp = await client.get("/api/transactions", headers=auth_headers, params=params)
    assert resp.json()["items"] == []


async def _panel_total(client, auth_headers, user_id, category_id) -> float:
    """Sum a category's drill-down the way the panel does with ownShare on:
    a member keeps their share, the owner the amount less the other shares,
    and the primary amount scales by the same ratio."""
    resp = await client.get(
        "/api/transactions", headers=auth_headers,
        params={
            "category_id": str(category_id), "type": "debit", "status": "posted",
            "user_pnl_only": "true", "from": "2025-02-01", "to": "2025-02-28",
        },
    )
    assert resp.status_code == 200
    total = 0.0
    for tx in resp.json()["items"]:
        amount = float(tx["amount"])
        primary = float(tx["amount_primary"] if tx["amount_primary"] is not None else amount)
        splits = tx.get("splits") or []
        viewer_share = float(tx["viewer_share"] or 0)
        if tx["is_shared"]:
            share = viewer_share
        elif splits and tx["user_id"] == str(user_id):
            share = amount - (sum(float(s["share_amount"]) for s in splits) - viewer_share)
        else:
            share = amount
        total -= primary * share / amount
    return round(total, 2)


@pytest.mark.asyncio
async def test_owner_split_in_foreign_currency_uses_the_stored_rate(
    session: AsyncSession, client, auth_headers, test_user, test_workspace, test_categories
):
    """A USD 100 expense imported at 5 BRL/USD, split 50/50 with a friend,
    reads -250 even when today's rate is 6, and the panel agrees."""
    food = test_categories[0]
    session.add(FxRate(
        id=uuid.uuid4(), base_currency="USD", quote_currency="BRL",
        rate=Decimal("6"), date=app_today(), source="test",
    ))
    group = Group(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Trip")
    session.add(group)
    await session.flush()
    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Me", is_self=True)
    friend = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Friend")
    session.add_all([me, friend])
    account = await _account(session, test_user.id)
    tx = _txn(
        test_user.id, account, "100", date(2025, 2, 10), "debit", food.id,
        currency="USD", amount_primary=Decimal("500"),
    )
    session.add(tx)
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=me.id, share_amount=Decimal("50")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=friend.id, share_amount=Decimal("50")),
    ])
    await session.commit()

    statement = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    [row] = statement.expenses.rows
    assert row.values == [-250.0]
    assert await _panel_total(client, auth_headers, test_user.id, food.id) == -250.0


@pytest.mark.asyncio
async def test_payer_in_someone_elses_group_counts_their_own_share(
    session: AsyncSession, client, auth_headers, test_user, test_workspace, test_categories
):
    """The user pays 100 in a group another workspace member created, split
    70 to the creator and 30 to the user. Cell and panel both read -30, not
    the creator's 70."""
    food = test_categories[0]
    creator = await _workspace_member(session, test_workspace.id, "creator@example.com")
    group = Group(id=uuid.uuid4(), user_id=creator.id, workspace_id=test_workspace.id, name="Flat")
    session.add(group)
    await session.flush()
    creator_member = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, name="Creator", is_self=True,
    )
    user_member = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
        name="User", linked_user_id=test_user.id,
    )
    session.add_all([creator_member, user_member])
    account = await _account(session, test_user.id)
    tx = _txn(test_user.id, account, "100", date(2025, 2, 10), "debit", food.id)
    session.add(tx)
    await session.flush()
    session.add_all([
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=creator_member.id, share_amount=Decimal("70")),
        TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id, workspace_id=test_workspace.id,
                         group_member_id=user_member.id, share_amount=Decimal("30")),
    ])
    await session.commit()

    statement = await get_category_statement(
        session, test_workspace.id, test_user.id, date(2025, 2, 1), months=1,
        primary_currency="BRL",
    )
    [row] = statement.expenses.rows
    assert row.values == [-30.0]
    assert await _panel_total(client, auth_headers, test_user.id, food.id) == -30.0
