"""Coverage-focused tests for app/api/accounts.py.

Exercises list filters, get/create/update/delete, close/reopen, summary,
balance-history and bills endpoints, plus 404/400 error branches and the
non-primary-currency conversion path.
"""

import pytest
from httpx import AsyncClient

from app.models.account import Account


NONEXISTENT = "00000000-0000-0000-0000-000000000000"


# ---------------------------------------------------------------------------
# list_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_include_closed(client: AsyncClient, auth_headers):
    """A manually-closed account is hidden by default and shown with the flag."""
    create = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Closeable", "type": "savings", "balance": "10.00"},
    )
    acc_id = create.json()["id"]
    await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)

    default = await client.get("/api/accounts", headers=auth_headers)
    assert all(a["id"] != acc_id for a in default.json())

    with_closed = await client.get("/api/accounts?include_closed=true", headers=auth_headers)
    assert any(a["id"] == acc_id for a in with_closed.json())


@pytest.mark.asyncio
async def test_list_accounts_foreign_currency_conversion(client: AsyncClient, auth_headers):
    """A non-primary-currency account triggers the convert() balance_primary path."""
    await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "USD Wallet", "type": "checking", "balance": "100.00", "currency": "USD"},
    )
    resp = await client.get("/api/accounts", headers=auth_headers)
    assert resp.status_code == 200
    usd = next(a for a in resp.json() if a["name"] == "USD Wallet")
    assert "balance_primary" in usd


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_found(client: AsyncClient, auth_headers, test_account: Account):
    resp = await client.get(f"/api/accounts/{test_account.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Conta Corrente"


# ---------------------------------------------------------------------------
# create / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_credit_card_account(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Visa",
            "type": "credit_card",
            "balance": "0.00",
            "credit_limit": "5000.00",
            "statement_close_day": 10,
            "payment_due_day": 18,
            "card_brand": "visa",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["type"] == "credit_card"
    assert data["credit_limit"] == 5000.0


@pytest.mark.asyncio
async def test_update_account_name(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Old", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]
    resp = await client.patch(
        f"/api/accounts/{acc_id}",
        headers=auth_headers,
        json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_update_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(
        f"/api/accounts/{NONEXISTENT}",
        headers=auth_headers,
        json={"name": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    """test_account is connection-backed; editing the name must 400."""
    resp = await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"name": "Hacked"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_account(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Temp", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]
    resp = await client.delete(f"/api/accounts/{acc_id}", headers=auth_headers)
    assert resp.status_code == 204
    # Gone now
    assert (await client.get(f"/api/accounts/{acc_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_delete_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.delete(f"/api/accounts/{NONEXISTENT}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    resp = await client.delete(f"/api/accounts/{test_account.id}", headers=auth_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# close / reopen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_then_reopen(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Lifecycle", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]

    closed = await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)
    assert closed.status_code == 200
    assert closed.json()["is_closed"] is True

    # Double-close is a 400
    again = await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)
    assert again.status_code == 400

    reopened = await client.post(f"/api/accounts/{acc_id}/reopen", headers=auth_headers)
    assert reopened.status_code == 200
    assert reopened.json()["is_closed"] is False

    # Reopen an open account is a 400
    again2 = await client.post(f"/api/accounts/{acc_id}/reopen", headers=auth_headers)
    assert again2.status_code == 400


@pytest.mark.asyncio
async def test_close_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(f"/api/accounts/{NONEXISTENT}/close", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reopen_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(f"/api/accounts/{NONEXISTENT}/reopen", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_summary(client: AsyncClient, auth_headers, test_transactions):
    account_id = test_transactions[0].account_id
    resp = await client.get(f"/api/accounts/{account_id}/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "current_balance" in data
    assert "monthly_income" in data
    assert "monthly_expenses" in data


@pytest.mark.asyncio
async def test_account_summary_with_date_range(
    client: AsyncClient, auth_headers, test_transactions
):
    account_id = test_transactions[0].account_id
    resp = await client.get(
        f"/api/accounts/{account_id}/summary?from=2020-01-01&to=2020-12-31",
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_account_summary_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/accounts/{NONEXISTENT}/summary", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# balance-history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_history(client: AsyncClient, auth_headers, test_transactions):
    account_id = test_transactions[0].account_id
    resp = await client.get(f"/api/accounts/{account_id}/balance-history", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_balance_history_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/accounts/{NONEXISTENT}/balance-history", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# bills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_bills_empty_for_checking(
    client: AsyncClient, auth_headers, test_account: Account
):
    """Non-CC accounts return an empty bills list (not a 404)."""
    resp = await client.get(f"/api/accounts/{test_account.id}/bills", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_account_bills_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/accounts/{NONEXISTENT}/bills", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_requires_auth(client: AsyncClient):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 401
