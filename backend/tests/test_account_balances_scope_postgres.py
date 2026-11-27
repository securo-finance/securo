"""The account list reads its own workspace's balances only, on real PostgreSQL."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.services import account_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


async def _user_and_workspace(session, name):
    user = User(
        id=uuid.uuid4(), email=f"{name}@example.com", hashed_password="x", preferences={},
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(id=uuid.uuid4(), name=name, created_by_user_id=user.id)
    session.add(workspace)
    await session.flush()
    return user, workspace


async def _account(session, user, workspace, name, **extra):
    extra.setdefault("type", "checking")
    extra.setdefault("balance", Decimal("0"))
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        currency="BRL", **extra,
    )
    session.add(account)
    await session.flush()
    return account


def _txn(user, workspace, account, amount, txn_type, when, **extra):
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=account.id,
        description=f"{txn_type} {amount}", amount=Decimal(amount), currency="BRL",
        type=txn_type, date=when, effective_date=when, source="manual", **extra,
    )


@pytest_asyncio.fixture
async def data(session):
    today = app_today()
    last_month = today.replace(day=1) - timedelta(days=5)
    user, home = await _user_and_workspace(session, "home")
    other_user, other = await _user_and_workspace(session, "other")

    checking = await _account(session, user, home, "Checking")
    card = await _account(
        session, user, home, "Card", type="credit_card", shared_balance_group="line",
        credit_limit=Decimal("5000"),
    )
    # A connection of this workspace that feeds an account kept in another one:
    # the list shows it here, so its balance has to be read here too.
    connection = BankConnection(
        id=uuid.uuid4(), user_id=user.id, workspace_id=home.id, provider="test",
        external_id="conn", institution_name="Bank", credentials={}, status="active",
    )
    session.add(connection)
    await session.flush()
    linked = await _account(
        session, other_user, other, "Linked", connection_id=connection.id, balance=Decimal("90"),
    )
    foreign = await _account(
        session, other_user, other, "Foreign", type="credit_card", shared_balance_group="line",
    )
    ignored = Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=home.id, name="Ignored", is_ignored=True,
    )
    session.add(ignored)
    await session.flush()

    session.add_all([
        _txn(user, home, checking, "1000", "credit", last_month),
        _txn(user, home, checking, "300", "debit", today),
        _txn(user, home, checking, "70", "debit", today, category_id=ignored.id),
        # Written from the other workspace into this account: still its balance.
        _txn(other_user, other, checking, "25", "debit", today),
        _txn(user, home, card, "400", "debit", last_month),
        _txn(user, home, card, "150", "debit", today + timedelta(days=40)),
        _txn(other_user, other, linked, "90", "credit", last_month),
        _txn(other_user, other, foreign, "9999", "debit", last_month),
        _txn(other_user, other, foreign, "8888", "debit", today + timedelta(days=40)),
    ])
    for account, owner, workspace, days, total in (
        (card, user, home, 5, "320"),
        (card, user, home, 35, "800"),
        (foreign, other_user, other, 5, "9999"),
    ):
        session.add(CreditCardBill(
            id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id, account_id=account.id,
            external_id=f"{account.name}-{days}", due_date=today + timedelta(days=days),
            total_amount=Decimal(total), currency="BRL",
        ))
    await session.commit()
    return {
        "home": home, "other": other, "checking": checking, "card": card,
        "linked": linked, "foreign": foreign,
    }


async def test_list_and_single_read_agree_with_a_card_and_a_linked_account(session, data):
    home = data["home"].id
    rows = {row["name"]: row for row in await account_service.get_accounts(session, home)}

    assert set(rows) == {"Checking", "Card", "Linked"}
    assert rows["Checking"]["current_balance"] == 675
    assert rows["Checking"]["previous_balance"] == 1000
    assert rows["Card"]["current_balance"] == -400
    assert rows["Card"]["previous_balance"] == -400
    assert rows["Card"]["open_bill_total"] == 320
    assert rows["Card"]["available_credit"] == 4450
    assert rows["Linked"]["current_balance"] == 90
    assert rows["Linked"]["previous_balance"] == 90

    for name in ("checking", "card", "linked"):
        single = await account_service.get_account_with_balances(session, data[name].id, home)
        assert single == rows[data[name].name]


async def test_an_account_of_another_workspace_is_not_read(session, data):
    home, other = data["home"].id, data["other"].id
    assert await account_service.get_account_with_balances(session, data["foreign"].id, home) is None
    rows = {row["name"]: row for row in await account_service.get_accounts(session, other)}
    assert set(rows) == {"Linked", "Foreign"}
    assert rows["Foreign"]["current_balance"] == -9999
    assert rows["Foreign"]["open_bill_total"] == 9999


async def test_account_ids_in_scope(session, data):
    home = data["home"].id
    scope = account_service._account_ids_in_scope
    assert set(await scope(session, home)) == {
        data["checking"].id, data["card"].id, data["linked"].id,
    }
    assert await scope(session, home, account_id=data["linked"].id) == [data["linked"].id]
    assert await scope(session, home, account_id=data["foreign"].id) == []

    data["checking"].is_closed = True
    data["linked"].is_closed = True
    await session.commit()
    assert await scope(session, home, include_closed=False) == [data["card"].id]
    names = {row["name"] for row in await account_service.get_accounts(session, home)}
    assert names == {"Card"}
    names = {row["name"] for row in await account_service.get_accounts(session, home, True)}
    assert names == {"Checking", "Card", "Linked"}


async def test_a_workspace_without_accounts_lists_none(session, data):
    _, empty = await _user_and_workspace(session, "empty")
    assert await account_service.get_accounts(session, empty.id) == []


@pytest.mark.parametrize("single", [False, True])
async def test_balance_subqueries_read_only_the_accounts_in_scope(session, data, single):
    """Counts what each subquery returns: the totals alone would hide the extra accounts."""
    account_id = data["card"].id if single else None
    expected = {data["card"].id} if single else {
        data["checking"].id, data["card"].id, data["linked"].id,
    }
    ids = await account_service._account_ids_in_scope(
        session, data["home"].id, account_id=account_id
    )
    subqueries = account_service._balance_subqueries(app_today(), ids)
    read = [
        {row.account_id for row in (await session.execute(sq.select())).all()}
        for sq in subqueries
    ]
    balance, previous, future, bills = read
    assert balance == previous == expected
    assert future == bills == {data["card"].id}


async def test_an_account_moved_after_its_id_was_read_is_not_returned(session, data, monkeypatch):
    """A sync can move an account between the id lookup and the balance read."""
    home, other = data["home"].id, data["other"].id
    checking = data["checking"]
    read_ids = account_service._account_ids_in_scope

    async def ids_then_move(*args, **kwargs):
        ids = await read_ids(*args, **kwargs)
        checking.workspace_id = other
        await session.flush()
        return ids

    monkeypatch.setattr(account_service, "_account_ids_in_scope", ids_then_move)

    names = {row["name"] for row in await account_service.get_accounts(session, home)}
    assert names == {"Card", "Linked"}
    checking.workspace_id = home
    await session.flush()
    assert await account_service.get_account_with_balances(session, checking.id, home) is None
