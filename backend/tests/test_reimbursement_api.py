"""The reimbursement routes under /api/transactions (#1126)."""

from decimal import Decimal

import pytest
from httpx import AsyncClient


async def _account(client: AsyncClient, headers) -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Wallet", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create(client: AsyncClient, headers, account_id, amount, type_="debit", **extra) -> dict:
    payload = {
        "account_id": account_id,
        "description": extra.pop("description", "Dinner"),
        "amount": amount,
        "date": extra.pop("date", "2026-10-10"),
        "type": type_,
        "currency": "USD",
        **extra,
    }
    resp = await client.post("/api/transactions", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _body(*pairs) -> dict:
    return {"allocations": [{"counterpart_id": tx["id"], "amount": amount} for tx, amount in pairs]}


@pytest.mark.asyncio
async def test_link_and_read_from_both_sides(client, auth_headers):
    account = await _account(client, auth_headers)
    dinner = await _create(client, auth_headers, account["id"], 100)
    alice = await _create(client, auth_headers, account["id"], 33, "credit", description="Alice")
    bob = await _create(client, auth_headers, account["id"], 50, "credit", description="Bob")

    resp = await client.put(
        f"/api/transactions/{dinner['id']}/reimbursements",
        headers=auth_headers,
        json=_body((alice, 33), (bob, 33)),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["net_amount"]) == Decimal("34")
    assert {c["transaction"]["description"] for c in body["counterparts"]} == {"Alice", "Bob"}

    resp = await client.get(f"/api/transactions/{bob['id']}/reimbursements", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert [a["anchor_id"] for a in body["anchors"]] == [dinner["id"]]
    assert Decimal(body["unallocated"]) == Decimal("17")


@pytest.mark.asyncio
async def test_an_invariant_breaks_with_a_400(client, auth_headers):
    account = await _account(client, auth_headers)
    dinner = await _create(client, auth_headers, account["id"], 100)
    other = await _create(client, auth_headers, account["id"], 20)

    resp = await client.put(
        f"/api/transactions/{dinner['id']}/reimbursements",
        headers=auth_headers,
        json=_body((other, 20)),
    )

    assert resp.status_code == 400
    assert "opposite direction" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_amount_finer_than_cents_is_refused(client, auth_headers):
    account = await _account(client, auth_headers)
    dinner = await _create(client, auth_headers, account["id"], 100)
    payback = await _create(client, auth_headers, account["id"], 20, "credit")

    resp = await client.put(
        f"/api/transactions/{dinner['id']}/reimbursements",
        headers=auth_headers,
        json=_body((payback, "10.001")),
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_a_viewer_can_read_but_not_link(
    client, auth_headers, viewer_auth_headers, test_workspace
):
    account = await _account(client, auth_headers)
    dinner = await _create(client, auth_headers, account["id"], 100)
    payback = await _create(client, auth_headers, account["id"], 20, "credit")
    headers = {**viewer_auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    resp = await client.put(
        f"/api/transactions/{dinner['id']}/reimbursements", headers=headers, json=_body((payback, 20))
    )
    assert resp.status_code == 403
    resp = await client.get(f"/api/transactions/{dinner['id']}/reimbursements", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_an_unknown_transaction_is_a_404(client, auth_headers):
    resp = await client.get(
        "/api/transactions/00000000-0000-0000-0000-000000000000/reimbursements",
        headers=auth_headers,
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_candidates_go_the_other_way_and_skip_what_cannot_link(client, auth_headers):
    account = await _account(client, auth_headers)
    dinner = await _create(client, auth_headers, account["id"], 100)
    near = await _create(client, auth_headers, account["id"], 30, "credit", date="2026-10-12")
    far = await _create(client, auth_headers, account["id"], 30, "credit", date="2026-10-30")
    await _create(client, auth_headers, account["id"], 30, "credit", date="2027-03-01", description="Later")
    await _create(client, auth_headers, account["id"], 30, date="2026-10-11")
    ignored = await _create(client, auth_headers, account["id"], 30, "credit", date="2026-10-11")
    await client.patch(
        f"/api/transactions/{ignored['id']}/ignore", headers=auth_headers, json={"is_ignored": True}
    )
    spent = await _create(client, auth_headers, account["id"], 10, "credit", date="2026-10-11")
    other_dinner = await _create(client, auth_headers, account["id"], 10)
    resp = await client.put(
        f"/api/transactions/{other_dinner['id']}/reimbursements",
        headers=auth_headers,
        json=_body((spent, 10)),
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(
        f"/api/transactions/{dinner['id']}/reimbursement-candidates", headers=auth_headers
    )

    assert resp.status_code == 200
    assert [row["id"] for row in resp.json()] == [near["id"], far["id"]]

    resp = await client.get(
        f"/api/transactions/{dinner['id']}/reimbursement-candidates",
        headers=auth_headers,
        params={"q": "Later"},
    )
    assert [row["description"] for row in resp.json()] == ["Later"]


@pytest.mark.asyncio
async def test_candidate_anchors_for_a_payback(client, auth_headers):
    account = await _account(client, auth_headers)
    payback = await _create(client, auth_headers, account["id"], 30, "credit")
    dinner = await _create(client, auth_headers, account["id"], 100, date="2026-10-09")

    resp = await client.get(
        f"/api/transactions/{payback['id']}/reimbursement-candidates",
        headers=auth_headers,
        params={"looking_for": "anchors"},
    )

    assert [row["id"] for row in resp.json()] == [dinner["id"]]


def test_access_tokens_reach_the_routes_through_the_transactions_scope():
    from app.core.access_token_scopes import required_scope

    path = "/api/transactions/00000000-0000-0000-0000-000000000000"
    assert required_scope("GET", f"{path}/reimbursements") == "transactions:read"
    assert required_scope("GET", f"{path}/reimbursement-candidates") == "transactions:read"
    assert required_scope("PUT", f"{path}/reimbursements") == "transactions:write"
