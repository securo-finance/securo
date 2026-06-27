import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_payee(client: AsyncClient, auth_headers: dict, name: str, **kwargs) -> dict:
    resp = await client.post(
        "/api/payees", headers=auth_headers,
        json={"name": name, **kwargs},
    )
    assert resp.status_code == 201
    return resp.json()


async def _make_account_and_tx(
    session: AsyncSession, user: User, payee_id: str,
    amount: Decimal = Decimal("10"), tx_type: str = "debit",
) -> None:
    # Reuse existing account if possible, otherwise create
    from sqlalchemy import select
    result = await session.execute(
        select(Account).where(Account.user_id == user.id).limit(1)
    )
    account = result.scalar_one_or_none()
    if not account:
        account = Account(
            id=uuid.uuid4(), user_id=user.id,
            name="API Test Account", type="checking",
            balance=Decimal("1000"), currency="BRL",
        )
        session.add(account)
        await session.flush()

    session.add(Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id,
        description="API Test Tx", amount=amount, date=date.today(),
        type=tx_type, source="manual", payee_id=uuid.UUID(payee_id),
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_payees_empty(client: AsyncClient, auth_headers):
    resp = await client.get("/api/payees", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_payees(client: AsyncClient, auth_headers):
    await _create_payee(client, auth_headers, "Alpha")
    await _create_payee(client, auth_headers, "Beta")

    resp = await client.get("/api/payees", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {p["name"] for p in data}
    assert names == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_list_payees_filters(client: AsyncClient, auth_headers):
    mcd = await _create_payee(client, auth_headers, "McDonalds", type="merchant", notes="Burgers")
    # Toggle favorite via PATCH since PayeeCreate doesn't take is_favorite
    patch_resp = await client.patch(f"/api/payees/{mcd['id']}", headers=auth_headers, json={"is_favorite": True})
    assert patch_resp.status_code == 200

    await _create_payee(client, auth_headers, "John Doe", type="person", notes="Friend")
    await _create_payee(client, auth_headers, "Acme Corp", type="company", notes="Supplies")

    # Test search query on name
    resp = await client.get("/api/payees?q=Don", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "McDonalds"

    # Test search query on notes
    resp = await client.get("/api/payees?q=Friend", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "John Doe"

    # Test type filter
    resp = await client.get("/api/payees?type=person", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "John Doe"

    # Test favorites filter
    resp = await client.get("/api/payees?is_favorite=true", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "McDonalds"

    # Test search + favorites filter
    resp = await client.get("/api/payees?q=corp&is_favorite=true", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 0


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_payee(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/payees", headers=auth_headers,
        json={"name": "Starbucks", "type": "merchant", "notes": "Coffee"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Starbucks"
    assert data["type"] == "merchant"
    assert data["notes"] == "Coffee"
    assert data["is_favorite"] is False
    assert data["transaction_count"] == 0
    assert "id" in data


@pytest.mark.asyncio
async def test_create_payee_duplicate(client: AsyncClient, auth_headers):
    await _create_payee(client, auth_headers, "Unique")
    resp = await client.post(
        "/api/payees", headers=auth_headers,
        json={"name": "unique"},  # case-insensitive duplicate
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Get single
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_payee(client: AsyncClient, auth_headers):
    created = await _create_payee(client, auth_headers, "Single")
    resp = await client.get(f"/api/payees/{created['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Single"


@pytest.mark.asyncio
async def test_get_payee_not_found(client: AsyncClient, auth_headers):
    resp = await client.get(
        f"/api/payees/{uuid.uuid4()}", headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_payee(client: AsyncClient, auth_headers):
    created = await _create_payee(client, auth_headers, "OldName")
    resp = await client.patch(
        f"/api/payees/{created['id']}", headers=auth_headers,
        json={"name": "NewName", "is_favorite": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "NewName"
    assert data["is_favorite"] is True
    assert data["type"] == "merchant"  # unchanged


@pytest.mark.asyncio
async def test_update_payee_not_found(client: AsyncClient, auth_headers):
    resp = await client.patch(
        f"/api/payees/{uuid.uuid4()}", headers=auth_headers,
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_payee(client: AsyncClient, auth_headers):
    created = await _create_payee(client, auth_headers, "ToDelete")
    resp = await client.delete(
        f"/api/payees/{created['id']}", headers=auth_headers,
    )
    assert resp.status_code == 204

    # Confirm gone
    resp = await client.get(f"/api/payees/{created['id']}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_payee_not_found(client: AsyncClient, auth_headers):
    resp = await client.delete(
        f"/api/payees/{uuid.uuid4()}", headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_payees(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    target = await _create_payee(client, auth_headers, "MergeTarget")
    source = await _create_payee(client, auth_headers, "MergeSource")

    # Create a transaction linked to source
    await _make_account_and_tx(session, test_user, source["id"])

    resp = await client.post(
        "/api/payees/merge", headers=auth_headers,
        json={"target_id": target["id"], "source_ids": [source["id"]]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["transactions_reassigned"] == 1

    # Source should be gone
    resp = await client.get(f"/api/payees/{source['id']}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_merge_payees_invalid_target(client: AsyncClient, auth_headers):
    source = await _create_payee(client, auth_headers, "SrcOnly")
    resp = await client.post(
        "/api/payees/merge", headers=auth_headers,
        json={"target_id": str(uuid.uuid4()), "source_ids": [source["id"]]},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_payee_summary(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    payee = await _create_payee(client, auth_headers, "SummaryPayee")
    await _make_account_and_tx(session, test_user, payee["id"], Decimal("100"), "debit")
    await _make_account_and_tx(session, test_user, payee["id"], Decimal("30"), "credit")

    resp = await client.get(
        f"/api/payees/{payee['id']}/summary", headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["transaction_count"] == 2
    assert float(data["total_spent"]) == 100.0
    assert float(data["total_received"]) == 30.0


@pytest.mark.asyncio
async def test_get_payee_summary_not_found(client: AsyncClient, auth_headers):
    resp = await client.get(
        f"/api/payees/{uuid.uuid4()}/summary", headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payees_unauthenticated(client: AsyncClient, clean_db):
    resp = await client.get("/api/payees")
    assert resp.status_code == 401
