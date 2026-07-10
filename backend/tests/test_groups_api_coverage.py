"""Coverage-focused API tests for app/api/groups.py.

Complements test_groups_api.py: exercises update/delete, transactions,
member 404 branches, settlement update + 404s, and the various not-found
paths across the router.
"""

import uuid

import pytest
from httpx import AsyncClient


async def _create_group(client: AsyncClient, auth_headers: dict, **overrides) -> dict:
    payload = {"name": "Cov Group", "kind": "social", "default_currency": "USD"}
    payload.update(overrides)
    resp = await client.post("/api/groups", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_member(client, auth_headers, group_id, **fields) -> dict:
    payload = {"name": "Member"}
    payload.update(fields)
    resp = await client.post(f"/api/groups/{group_id}/members", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# update / delete group
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_group(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="Before")
    resp = await client.patch(
        f"/api/groups/{group['id']}",
        headers=auth_headers,
        json={"name": "After", "color": "#FF0000", "notes": "hi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "After"
    assert body["color"] == "#FF0000"
    assert body["notes"] == "hi"


@pytest.mark.asyncio
async def test_update_group_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.patch(
        f"/api/groups/{uuid.uuid4()}", headers=auth_headers, json={"name": "X"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_group_duplicate_name_400(client: AsyncClient, auth_headers, test_user):
    await _create_group(client, auth_headers, name="Taken")
    other = await _create_group(client, auth_headers, name="Other")
    resp = await client.patch(
        f"/api/groups/{other['id']}", headers=auth_headers, json={"name": "Taken"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_group(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="ToDelete")
    resp = await client.delete(f"/api/groups/{group['id']}", headers=auth_headers)
    assert resp.status_code == 204
    # Now gone.
    resp = await client.get(f"/api/groups/{group['id']}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_group_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.delete(f"/api/groups/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_group_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_members_unknown_group_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}/members", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_member_unknown_group_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        f"/api/groups/{uuid.uuid4()}/members",
        headers=auth_headers,
        json={"name": "Ghost"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_members_returns_added(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="HasMembers")
    await _add_member(client, auth_headers, group["id"], name="Bob")
    resp = await client.get(f"/api/groups/{group['id']}/members", headers=auth_headers)
    assert resp.status_code == 200
    assert any(m["name"] == "Bob" for m in resp.json())


@pytest.mark.asyncio
async def test_update_member_not_found_404(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="MemUpd")
    resp = await client.patch(
        f"/api/groups/{group['id']}/members/{uuid.uuid4()}",
        headers=auth_headers,
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_member_not_found_404(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="MemDel")
    resp = await client.delete(
        f"/api/groups/{group['id']}/members/{uuid.uuid4()}", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_member_validation_422(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="MemVal")
    resp = await client.post(
        f"/api/groups/{group['id']}/members", headers=auth_headers, json={"name": ""}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_group_transactions_empty(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="Txns")
    resp = await client.get(f"/api/groups/{group['id']}/transactions?limit=5", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_group_transactions_unknown_group_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}/transactions", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# balances
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_balances_unknown_group_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}/balances", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# settlements
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_settlements_unknown_group_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}/settlements", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_settlement_full_lifecycle(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="SettCov")
    a = await _add_member(client, auth_headers, group["id"], name="A")
    b = await _add_member(client, auth_headers, group["id"], name="B")

    create = await client.post(
        f"/api/groups/{group['id']}/settlements",
        headers=auth_headers,
        json={
            "from_member_id": a["id"],
            "to_member_id": b["id"],
            "amount": "20.00",
            "currency": "USD",
            "date": "2026-05-01",
        },
    )
    assert create.status_code == 201, create.text
    settlement_id = create.json()["id"]

    upd = await client.patch(
        f"/api/groups/{group['id']}/settlements/{settlement_id}",
        headers=auth_headers,
        json={"amount": "25.00"},
    )
    assert upd.status_code == 200, upd.text
    assert float(upd.json()["amount"]) == 25.00

    listing = await client.get(f"/api/groups/{group['id']}/settlements", headers=auth_headers)
    assert listing.status_code == 200
    assert any(s["id"] == settlement_id for s in listing.json())

    delete = await client.delete(
        f"/api/groups/{group['id']}/settlements/{settlement_id}", headers=auth_headers
    )
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_create_settlement_unknown_member_400(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="SettBad")
    a = await _add_member(client, auth_headers, group["id"], name="A")
    resp = await client.post(
        f"/api/groups/{group['id']}/settlements",
        headers=auth_headers,
        json={
            "from_member_id": a["id"],
            "to_member_id": str(uuid.uuid4()),
            "amount": "10.00",
            "currency": "USD",
            "date": "2026-05-01",
        },
    )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_update_settlement_not_found_404(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="SettUpd404")
    resp = await client.patch(
        f"/api/groups/{group['id']}/settlements/{uuid.uuid4()}",
        headers=auth_headers,
        json={"amount": "5.00"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_settlement_not_found_404(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="SettDel404")
    resp = await client.delete(
        f"/api/groups/{group['id']}/settlements/{uuid.uuid4()}", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_groups_include_archived(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="Archivable")
    # Archive via update.
    upd = await client.patch(
        f"/api/groups/{group['id']}", headers=auth_headers, json={"is_archived": True}
    )
    assert upd.status_code == 200
    # Not in default listing.
    default = await client.get("/api/groups", headers=auth_headers)
    assert not any(g["id"] == group["id"] for g in default.json())
    # Present with include_archived.
    archived = await client.get("/api/groups?include_archived=true", headers=auth_headers)
    assert any(g["id"] == group["id"] for g in archived.json())
