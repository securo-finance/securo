"""Collections feature (issue #105) — API + service coverage.

Collections are user-defined, workspace-scoped, many-to-many groups of
accounts used to filter the app. These tests exercise CRUD, membership
replacement, account-count rollup and workspace isolation.
"""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset_group import AssetGroup
from app.models.user import User
from app.models.workspace import Workspace


async def _make_wallet(session: AsyncSession, user: User, name: str) -> AssetGroup:
    wallet = AssetGroup(id=uuid.uuid4(), user_id=user.id, name=name)
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def _make_account(session: AsyncSession, user: User, name: str) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        type="checking",
        balance=Decimal("100.00"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


@pytest.mark.asyncio
async def test_create_and_list_collection(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    a1 = await _make_account(session, test_user, "Acct A")
    a2 = await _make_account(session, test_user, "Acct B")

    resp = await client.post(
        "/api/collections",
        headers=auth_headers,
        json={"name": "Business", "color": "#10B981", "account_ids": [str(a1.id), str(a2.id)]},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "Business"
    assert created["color"] == "#10B981"
    assert created["account_count"] == 2
    assert set(created["account_ids"]) == {str(a1.id), str(a2.id)}

    listed = (await client.get("/api/collections", headers=auth_headers)).json()
    assert len(listed) == 1
    assert listed[0]["account_count"] == 2


@pytest.mark.asyncio
async def test_create_empty_collection_allowed(
    client: AsyncClient, auth_headers, test_user: User
):
    resp = await client.post(
        "/api/collections", headers=auth_headers, json={"name": "Empty"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["account_count"] == 0
    assert resp.json()["account_ids"] == []


@pytest.mark.asyncio
async def test_update_replaces_membership(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    a1 = await _make_account(session, test_user, "A1")
    a2 = await _make_account(session, test_user, "A2")
    a3 = await _make_account(session, test_user, "A3")

    cid = (await client.post(
        "/api/collections", headers=auth_headers,
        json={"name": "C", "account_ids": [str(a1.id), str(a2.id)]},
    )).json()["id"]

    # Replace membership with a different set + rename.
    resp = await client.patch(
        f"/api/collections/{cid}", headers=auth_headers,
        json={"name": "Renamed", "account_ids": [str(a3.id)]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["account_ids"] == [str(a3.id)]
    assert body["account_count"] == 1

    # Omitting account_ids leaves membership untouched.
    resp2 = await client.patch(
        f"/api/collections/{cid}", headers=auth_headers, json={"color": "#EF4444"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["account_ids"] == [str(a3.id)]
    assert resp2.json()["color"] == "#EF4444"


@pytest.mark.asyncio
async def test_delete_collection(client: AsyncClient, auth_headers, test_user: User):
    cid = (await client.post(
        "/api/collections", headers=auth_headers, json={"name": "Temp"}
    )).json()["id"]

    assert (await client.delete(f"/api/collections/{cid}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/collections", headers=auth_headers)).json() == []
    # Deleting again is a 404.
    assert (await client.delete(f"/api/collections/{cid}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_membership_scoped_to_workspace(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    """An account from another workspace can't be added — it's silently dropped
    so a collection can never reference cross-workspace accounts."""
    # Account in the user's workspace.
    mine = await _make_account(session, test_user, "Mine")

    # Account in a *different* workspace (not the user's).
    other_ws = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other_ws)
    await session.commit()
    foreign = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_ws.id,
        name="Foreign", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(foreign)
    await session.commit()

    resp = await client.post(
        "/api/collections", headers=auth_headers,
        json={"name": "Scoped", "account_ids": [str(mine.id), str(foreign.id)]},
    )
    assert resp.status_code == 201, resp.text
    # Only the in-workspace account survived.
    assert resp.json()["account_ids"] == [str(mine.id)]
    assert resp.json()["account_count"] == 1


@pytest.mark.asyncio
async def test_collection_with_wallets(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    acc = await _make_account(session, test_user, "Acct")
    w1 = await _make_wallet(session, test_user, "Investments")
    w2 = await _make_wallet(session, test_user, "Crypto")

    resp = await client.post(
        "/api/collections", headers=auth_headers,
        json={"name": "Wealth", "account_ids": [str(acc.id)], "wallet_ids": [str(w1.id), str(w2.id)]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["account_count"] == 1
    assert body["wallet_count"] == 2
    assert set(body["wallet_ids"]) == {str(w1.id), str(w2.id)}

    cid = body["id"]
    # Replace wallets, leave accounts untouched (account_ids omitted).
    upd = await client.patch(
        f"/api/collections/{cid}", headers=auth_headers, json={"wallet_ids": [str(w1.id)]}
    )
    assert upd.status_code == 200
    assert upd.json()["wallet_ids"] == [str(w1.id)]
    assert upd.json()["account_ids"] == [str(acc.id)]  # untouched

    # Clear wallets.
    cleared = await client.patch(
        f"/api/collections/{cid}", headers=auth_headers, json={"wallet_ids": []}
    )
    assert cleared.json()["wallet_count"] == 0


@pytest.mark.asyncio
async def test_same_account_and_wallet_shared_across_collections(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    """Membership is many-to-many: the same account or wallet can belong to
    any number of collections simultaneously."""
    acc = await _make_account(session, test_user, "Shared acct")
    wallet = await _make_wallet(session, test_user, "Shared wallet")

    payload = {"account_ids": [str(acc.id)], "wallet_ids": [str(wallet.id)]}
    c1 = (await client.post("/api/collections", headers=auth_headers, json={"name": "C1", **payload})).json()
    c2 = (await client.post("/api/collections", headers=auth_headers, json={"name": "C2", **payload})).json()
    c3 = (await client.post("/api/collections", headers=auth_headers, json={"name": "C3", **payload})).json()

    # All three independently reference the same account + wallet.
    for c in (c1, c2, c3):
        assert c["account_ids"] == [str(acc.id)]
        assert c["wallet_ids"] == [str(wallet.id)]

    listed = (await client.get("/api/collections", headers=auth_headers)).json()
    assert len(listed) == 3
    # Deleting one collection leaves the shared members intact in the others.
    await client.delete(f"/api/collections/{c1['id']}", headers=auth_headers)
    remaining = (await client.get("/api/collections", headers=auth_headers)).json()
    assert {c["name"] for c in remaining} == {"C2", "C3"}
    for c in remaining:
        assert c["account_ids"] == [str(acc.id)]
        assert c["wallet_ids"] == [str(wallet.id)]


@pytest.mark.asyncio
async def test_wallet_only_collection(
    client: AsyncClient, auth_headers, test_user: User, session: AsyncSession
):
    """A collection can hold only wallets (no accounts)."""
    w = await _make_wallet(session, test_user, "Only wallet")
    resp = await client.post(
        "/api/collections", headers=auth_headers,
        json={"name": "Wallet-only", "wallet_ids": [str(w.id)]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["account_count"] == 0
    assert body["account_ids"] == []
    assert body["wallet_count"] == 1
    assert body["wallet_ids"] == [str(w.id)]


@pytest.mark.asyncio
async def test_update_missing_collection_404(client: AsyncClient, auth_headers, test_user: User):
    resp = await client.patch(
        f"/api/collections/{uuid.uuid4()}", headers=auth_headers, json={"name": "x"}
    )
    assert resp.status_code == 404
