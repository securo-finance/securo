"""Coverage-focused tests for app/api/transactions.py.

Covers list filters, export (filtered + selection), get/create/update/delete,
ignore toggle, bulk endpoints, transfer/link/counterpart/candidates, and
error branches (404/400/422).
"""
from datetime import date

import pytest
from httpx import AsyncClient

from app.models.account import Account


NONEXISTENT = "00000000-0000-0000-0000-000000000000"


async def _manual_account(client: AsyncClient, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": name, "type": "checking", "balance": "0.00"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# list filters + summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_with_summary(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] is not None
    assert {"income", "expense", "net", "excluded", "currency"} <= data["summary"].keys()


@pytest.mark.asyncio
async def test_list_filter_by_type(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?type=credit", headers=auth_headers)
    assert resp.status_code == 200
    assert all(t["type"] == "credit" for t in resp.json()["items"])


@pytest.mark.asyncio
async def test_list_search_query(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?q=UBER", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert "UBER" in items[0]["description"]


@pytest.mark.asyncio
async def test_list_uncategorized_only(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?uncategorized=true", headers=auth_headers)
    assert resp.status_code == 200
    assert all(t["category_id"] is None for t in resp.json()["items"])


@pytest.mark.asyncio
async def test_list_amount_bounds_and_sort(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get(
        "/api/transactions?min_amount=40&max_amount=200&sort_by=amount&sort_dir=asc",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    amounts = [abs(float(t["amount"])) for t in items]
    assert amounts == sorted(amounts)
    assert all(40 <= a <= 200 for a in amounts)


@pytest.mark.asyncio
async def test_list_exclude_transfers(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?exclude_transfers=true", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_invalid_sort_dir_422(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?sort_dir=sideways", headers=auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_filtered(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "date,description,amount" in body
    assert "UBER TRIP" in body


@pytest.mark.asyncio
async def test_export_selection_only(client: AsyncClient, auth_headers, test_transactions):
    tx_id = str(test_transactions[0].id)
    resp = await client.get(
        f"/api/transactions/export?transaction_ids={tx_id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert test_transactions[0].description in body


# ---------------------------------------------------------------------------
# get / create / update / delete / ignore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transaction(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.get(f"/api/transactions/{tx.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["description"] == tx.description


@pytest.mark.asyncio
async def test_get_transaction_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/transactions/{NONEXISTENT}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_transaction(client: AsyncClient, auth_headers, test_account: Account):
    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "Padaria",
            "amount": "12.50",
            "date": date.today().isoformat(),
            "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["description"] == "Padaria"


@pytest.mark.asyncio
async def test_create_transaction_bad_account_400(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={
            "account_id": NONEXISTENT,
            "description": "Ghost",
            "amount": "1.00",
            "date": date.today().isoformat(),
            "type": "debit",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_transaction(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers,
        json={"description": "UBER updated", "notes": "trip"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "UBER updated"
    assert data["notes"] == "trip"


@pytest.mark.asyncio
async def test_update_transaction_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(
        f"/api/transactions/{NONEXISTENT}", headers=auth_headers,
        json={"description": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_toggle_ignore(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.patch(f"/api/transactions/{tx.id}/ignore", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_ignored"] is True


@pytest.mark.asyncio
async def test_toggle_ignore_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(f"/api/transactions/{NONEXISTENT}/ignore", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_transaction(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.delete(f"/api/transactions/{tx.id}", headers=auth_headers)
    assert resp.status_code == 204
    assert (await client.get(f"/api/transactions/{tx.id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_delete_transaction_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.delete(f"/api/transactions/{NONEXISTENT}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# bulk endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_categorize(client: AsyncClient, auth_headers, test_transactions, test_categories):
    ids = [str(t.id) for t in test_transactions[:2]]
    resp = await client.patch(
        "/api/transactions/bulk-categorize", headers=auth_headers,
        json={"transaction_ids": ids, "category_id": str(test_categories[0].id)},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2


@pytest.mark.asyncio
async def test_bulk_add_and_remove_tags(client: AsyncClient, auth_headers, test_transactions):
    ids = [str(t.id) for t in test_transactions[:2]]
    add = await client.patch(
        "/api/transactions/bulk-add-tags", headers=auth_headers,
        json={"transaction_ids": ids, "tags": ["viagem", "reembolso"]},
    )
    assert add.status_code == 200
    assert add.json()["updated"] == 2

    remove = await client.patch(
        "/api/transactions/bulk-remove-tags", headers=auth_headers,
        json={"transaction_ids": ids, "tags": ["viagem"]},
    )
    assert remove.status_code == 200
    assert remove.json()["updated"] == 2


@pytest.mark.asyncio
async def test_bulk_add_to_group_bad_group_400(client: AsyncClient, auth_headers, test_transactions):
    ids = [str(t.id) for t in test_transactions[:1]]
    resp = await client.patch(
        "/api/transactions/bulk-add-to-group", headers=auth_headers,
        json={"transaction_ids": ids, "group_id": NONEXISTENT, "share_type": "equal"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# transfer / link / counterpart / candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transfer(client: AsyncClient, auth_headers, test_account: Account):
    other = await _manual_account(client, auth_headers, "Destino")
    resp = await client.post(
        "/api/transactions/transfer", headers=auth_headers,
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": other,
            "amount": "100.00",
            "date": date.today().isoformat(),
            "description": "Transferência",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["debit"]["type"] == "debit"
    assert data["credit"]["type"] == "credit"
    assert data["transfer_pair_id"]


@pytest.mark.asyncio
async def test_create_transfer_same_account_400(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(
        "/api/transactions/transfer", headers=auth_headers,
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(test_account.id),
            "amount": "10.00",
            "date": date.today().isoformat(),
            "description": "self",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_transfer_candidates(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.get(
        f"/api/transactions/{tx.id}/transfer-candidates", headers=auth_headers
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_transfer_candidates_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(
        f"/api/transactions/{NONEXISTENT}/transfer-candidates", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_counterpart(
    client: AsyncClient, auth_headers, test_account: Account, test_transactions
):
    """Mark an existing tx as a transfer by auto-creating its counterpart."""
    other = await _manual_account(client, auth_headers, "Contrapartida")
    tx = test_transactions[0]
    resp = await client.post(
        f"/api/transactions/{tx.id}/create-counterpart", headers=auth_headers,
        json={"to_account_id": other},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["transfer_pair_id"]


@pytest.mark.asyncio
async def test_create_counterpart_bad_target_400(
    client: AsyncClient, auth_headers, test_transactions
):
    tx = test_transactions[0]
    resp = await client.post(
        f"/api/transactions/{tx.id}/create-counterpart", headers=auth_headers,
        json={"to_account_id": NONEXISTENT},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_transactions_requires_auth(client: AsyncClient):
    resp = await client.get("/api/transactions")
    assert resp.status_code == 401
