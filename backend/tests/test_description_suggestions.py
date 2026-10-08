"""Description autocomplete for the transaction form (issue #1127).

Suggestions are the distinct descriptions already used in the request's
workspace, each carrying the category and payee of its most recent use so
the form can prefill them.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

URL = "/api/transactions/description-suggestions"


def _tx(account: Account, workspace_id: uuid.UUID, description: str, days_ago: int, **kw) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=account.user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        currency="BRL",
        date=date.today() - timedelta(days=days_ago),
        type="debit",
        created_at=datetime.now(timezone.utc),
        **{"source": "manual", **kw},
    )


async def test_suggests_distinct_descriptions_with_latest_category(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_account: Account,
    test_workspace: Workspace,
    test_categories: list[Category],
):
    old_cat, new_cat = test_categories[0], test_categories[1]
    session.add_all([
        _tx(test_account, test_workspace.id, "Supermarket Extra", 30, category_id=old_cat.id),
        _tx(test_account, test_workspace.id, "supermarket extra ", 2, category_id=new_cat.id),
        _tx(test_account, test_workspace.id, "Big Supermarket", 1),
        _tx(test_account, test_workspace.id, "Pharmacy", 1),
    ])
    await session.commit()

    resp = await client.get(URL, params={"q": "super"}, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    # Prefix matches first, case-insensitive duplicates collapsed to the newest.
    assert [s["description"] for s in body] == ["supermarket extra", "Big Supermarket"]
    assert body[0]["category_id"] == str(new_cat.id)


async def test_scoped_to_the_request_workspace(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_account: Account,
    test_workspace: Workspace,
):
    other = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal",
        created_by_user_id=test_user.id, default_currency="BRL",
    )
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    session.add_all([
        _tx(test_account, test_workspace.id, "Coffee Shop", 1),
        _tx(test_account, other.id, "Coffee Roasters", 1),
    ])
    await session.commit()

    resp = await client.get(URL, params={"q": "coffee"}, headers=auth_headers)

    assert [s["description"] for s in resp.json()] == ["Coffee Shop"]


async def test_skips_ignored_opening_balance_and_short_queries(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_account: Account,
    test_workspace: Workspace,
):
    session.add_all([
        _tx(test_account, test_workspace.id, "Opening balance", 5, source="opening_balance"),
        _tx(test_account, test_workspace.id, "Open bar tab", 5, is_ignored=True),
        _tx(test_account, test_workspace.id, "Open air market", 5),
    ])
    await session.commit()

    resp = await client.get(URL, params={"q": "open"}, headers=auth_headers)
    assert [s["description"] for s in resp.json()] == ["Open air market"]

    short = await client.get(URL, params={"q": "o"}, headers=auth_headers)
    assert short.json() == []


async def test_like_wildcards_are_literal(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_account: Account,
    test_workspace: Workspace,
):
    session.add_all([
        _tx(test_account, test_workspace.id, "100% juice", 1),
        _tx(test_account, test_workspace.id, "1000 widgets", 1),
    ])
    await session.commit()

    resp = await client.get(URL, params={"q": "0%"}, headers=auth_headers)

    assert [s["description"] for s in resp.json()] == ["100% juice"]


async def test_older_prefix_match_is_not_crowded_out_by_recent_rows(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_account: Account,
    test_workspace: Workspace,
):
    # Many recent rows that only *contain* the query, one old prefix match.
    session.add_all(
        [_tx(test_account, test_workspace.id, "Supermarket", i) for i in range(1, 600)]
        + [_tx(test_account, test_workspace.id, "Market Hall", 900)]
    )
    await session.commit()

    resp = await client.get(URL, params={"q": "market"}, headers=auth_headers)

    assert [s["description"] for s in resp.json()] == ["Market Hall", "Supermarket"]
