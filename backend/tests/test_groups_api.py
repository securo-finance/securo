import pytest
from httpx import AsyncClient


async def _create_group(client: AsyncClient, auth_headers: dict, **overrides) -> dict:
    payload = {"name": "Roommates", "kind": "social", "default_currency": "USD"}
    payload.update(overrides)
    resp = await client.post("/api/groups", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_member(client, auth_headers, group_id, **fields) -> dict:
    payload = {"name": "Alice"}
    payload.update(fields)
    resp = await client.post(f"/api/groups/{group_id}/members", headers=auth_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_create_list_get_group(client: AsyncClient, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="My Group")

    resp = await client.get("/api/groups", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert any(g["id"] == group["id"] for g in body)

    resp = await client.get(f"/api/groups/{group['id']}", headers=auth_headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["name"] == "My Group"
    assert detail["members"] == []


@pytest.mark.asyncio
async def test_duplicate_group_name_returns_400(client, auth_headers, test_user):
    await _create_group(client, auth_headers, name="Same")
    resp = await client.post(
        "/api/groups",
        headers=auth_headers,
        json={"name": "Same", "kind": "social", "default_currency": "USD"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_kind_validation_rejects_unknown(client, auth_headers, test_user):
    resp = await client.post(
        "/api/groups",
        headers=auth_headers,
        json={"name": "X", "kind": "made_up", "default_currency": "USD"},
    )
    # Pydantic validation error -> 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_member_lifecycle(client, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="L")

    member = await _add_member(client, auth_headers, group["id"], name="Alice", is_self=True)
    assert member["is_self"] is True

    resp = await client.patch(
        f"/api/groups/{group['id']}/members/{member['id']}",
        headers=auth_headers,
        json={"name": "Alicia"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alicia"

    resp = await client.delete(
        f"/api/groups/{group['id']}/members/{member['id']}", headers=auth_headers
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_balances_endpoint_empty_group(client, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="EB")
    resp = await client.get(f"/api/groups/{group['id']}/balances", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"] == []
    assert body["self_member_id"] is None


@pytest.mark.asyncio
async def test_settlement_endpoints(client, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="Sett")
    a = await _add_member(client, auth_headers, group["id"], name="A")
    b = await _add_member(client, auth_headers, group["id"], name="B")

    resp = await client.post(
        f"/api/groups/{group['id']}/settlements",
        headers=auth_headers,
        json={
            "from_member_id": a["id"],
            "to_member_id": b["id"],
            "amount": "12.34",
            "currency": "USD",
            "date": "2026-04-28",
        },
    )
    assert resp.status_code == 201, resp.text
    settlement = resp.json()

    resp = await client.get(f"/api/groups/{group['id']}/settlements", headers=auth_headers)
    assert resp.status_code == 200
    assert any(s["id"] == settlement["id"] for s in resp.json())

    resp = await client.delete(
        f"/api/groups/{group['id']}/settlements/{settlement['id']}",
        headers=auth_headers,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_unauthorized_without_token(client):
    resp = await client.get("/api/groups")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_settlement_distinct_from_to_validation(client, auth_headers, test_user):
    group = await _create_group(client, auth_headers, name="Distinct")
    a = await _add_member(client, auth_headers, group["id"], name="A")

    resp = await client.post(
        f"/api/groups/{group['id']}/settlements",
        headers=auth_headers,
        json={
            "from_member_id": a["id"],
            "to_member_id": a["id"],
            "amount": "5.00",
            "currency": "USD",
            "date": "2026-04-28",
        },
    )
    # Pydantic validator -> 422
    assert resp.status_code == 422
