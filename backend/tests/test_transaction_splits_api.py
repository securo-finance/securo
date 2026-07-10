"""End-to-end-ish tests for transaction CRUD with splits attached."""

import pytest
from httpx import AsyncClient


async def _create_account(client: AsyncClient, auth_headers, name="Wallet") -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": name,
            "type": "checking",
            "balance": 0,
            "currency": "USD",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create_group_with_members(client, auth_headers, *names):
    g = (
        await client.post(
            "/api/groups",
            headers=auth_headers,
            json={"name": f"Trip-{names[0]}", "kind": "social", "default_currency": "USD"},
        )
    ).json()
    members = []
    for n in names:
        m = (
            await client.post(
                f"/api/groups/{g['id']}/members",
                headers=auth_headers,
                json={"name": n, "is_self": n == names[0]},
            )
        ).json()
        members.append(m)
    return g, members


@pytest.mark.asyncio
async def test_create_transaction_with_equal_split(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A", "B")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Dinner",
            "amount": 99,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [{"group_member_id": m["id"]} for m in members],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    splits = body["splits"]
    assert len(splits) == 3
    total = sum(float(s["share_amount"]) for s in splits)
    assert abs(total - 99.0) < 0.01


@pytest.mark.asyncio
async def test_update_transaction_replaces_splits(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A", "B")

    create = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "X",
            "amount": 60,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [{"group_member_id": m["id"]} for m in members],
            },
        },
    )
    tx_id = create.json()["id"]

    update = await client.patch(
        f"/api/transactions/{tx_id}",
        headers=auth_headers,
        json={
            "splits": {
                "share_type": "exact",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_amount": "20.00"},
                    {"group_member_id": members[1]["id"], "share_amount": "40.00"},
                ],
            }
        },
    )
    assert update.status_code == 200, update.text
    splits = update.json()["splits"]
    assert len(splits) == 2
    by_member = {s["group_member_id"]: float(s["share_amount"]) for s in splits}
    assert by_member[members[0]["id"]] == 20.0
    assert by_member[members[1]["id"]] == 40.0


@pytest.mark.asyncio
async def test_split_validation_bubbles_400(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Bad",
            "amount": 100,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "exact",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_amount": "30.00"},
                    {"group_member_id": members[1]["id"], "share_amount": "30.00"},
                ],
            },
        },
    )
    # ValueError from split_service -> 400 via FastAPI handler
    # (depends on the API layer's existing exception mapping; if it
    # surfaces as 500 that means the handler is missing — flag it).
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_create_rejects_percent_under_100(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Bad-pct",
            "amount": 100,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "percent",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_pct": "60"},
                    {"group_member_id": members[1]["id"], "share_pct": "30"},
                ],
            },
        },
    )
    assert resp.status_code == 400, resp.text
    assert "100" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_rejects_percent_over_100(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Bad-pct-over",
            "amount": 100,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "percent",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_pct": "60"},
                    {"group_member_id": members[1]["id"], "share_pct": "50"},
                ],
            },
        },
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_create_rejects_exact_under_total(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A", "B")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Bad-exact",
            "amount": 90,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "exact",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_amount": "30.00"},
                    {"group_member_id": members[1]["id"], "share_amount": "30.00"},
                ],
            },
        },
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_update_rejects_invalid_splits_and_keeps_old(client, auth_headers, test_user):
    """A failed split update must leave the transaction intact (no
    half-applied state) and keep the previous splits."""
    account = await _create_account(client, auth_headers)
    _, members = await _create_group_with_members(client, auth_headers, "Me", "A", "B")

    create = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Initial",
            "amount": 90,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [{"group_member_id": m["id"]} for m in members],
            },
        },
    )
    tx_id = create.json()["id"]
    original_splits = create.json()["splits"]

    bad = await client.patch(
        f"/api/transactions/{tx_id}",
        headers=auth_headers,
        json={
            "splits": {
                "share_type": "percent",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_pct": "20"},
                    {"group_member_id": members[1]["id"], "share_pct": "30"},
                ],
            }
        },
    )
    assert bad.status_code == 400, bad.text

    after = await client.get(f"/api/transactions/{tx_id}", headers=auth_headers)
    assert after.status_code == 200
    assert len(after.json()["splits"]) == len(original_splits)


@pytest.mark.asyncio
async def test_balances_reflect_splits(client, auth_headers, test_user):
    account = await _create_account(client, auth_headers)
    group, members = await _create_group_with_members(client, auth_headers, "Me", "A", "B")
    me, a, b = members

    await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Drinks",
            "amount": 60,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me["id"]},
                    {"group_member_id": a["id"]},
                    {"group_member_id": b["id"]},
                ],
            },
        },
    )

    resp = await client.get(f"/api/groups/{group['id']}/balances", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    by_member = {ln["member_id"]: float(ln["amount"]) for ln in body["lines"]}
    # 60 / 3 = 20 each non-self share
    assert by_member.get(a["id"]) == 20.0
    assert by_member.get(b["id"]) == 20.0
