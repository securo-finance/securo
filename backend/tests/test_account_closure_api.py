"""Tests for account closure and reopen endpoints (Phase 2)."""
import pytest
from httpx import AsyncClient

from app.models.account import Account


@pytest.mark.asyncio
async def test_close_manual_account(client: AsyncClient, auth_headers):
    """Closing a manual account sets is_closed=True and closed_at."""
    # Create a manual account
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Savings", "type": "savings", "balance": "1000.00", "currency": "BRL"},
    )
    assert resp.status_code == 201
    account_id = resp.json()["id"]

    # Close it
    close_resp = await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)
    assert close_resp.status_code == 200
    data = close_resp.json()
    assert data["is_closed"] is True
    assert data["closed_at"] is not None


@pytest.mark.asyncio
async def test_close_bank_connected_account_keeps_connection_link(
    client: AsyncClient, auth_headers, test_account: Account
):
    """Closing a bank-connected account preserves its connection link.

    The next sync looks up accounts by (connection_id, external_id); unlinking
    on close caused sync to treat the provider account as new and create a
    duplicate active row (issue #90). The closed flag alone is enough to keep
    sync from touching the account.
    """
    assert test_account.connection_id is not None
    original_connection_id = str(test_account.connection_id)

    close_resp = await client.post(
        f"/api/accounts/{test_account.id}/close", headers=auth_headers
    )
    assert close_resp.status_code == 200
    data = close_resp.json()
    assert data["is_closed"] is True
    assert data["connection_id"] == original_connection_id


@pytest.mark.asyncio
async def test_close_already_closed_account_fails(client: AsyncClient, auth_headers):
    """Closing an already-closed account returns 400."""
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Temp", "type": "checking", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    # Close it
    await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)

    # Try closing again
    close_resp = await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)
    assert close_resp.status_code == 400


@pytest.mark.asyncio
async def test_reopen_closed_account(client: AsyncClient, auth_headers):
    """Reopening a closed account sets is_closed=False and closed_at=None."""
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Reopen Test", "type": "checking", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    # Close then reopen
    await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)
    reopen_resp = await client.post(f"/api/accounts/{account_id}/reopen", headers=auth_headers)
    assert reopen_resp.status_code == 200
    data = reopen_resp.json()
    assert data["is_closed"] is False
    assert data["closed_at"] is None


@pytest.mark.asyncio
async def test_reopen_non_closed_account_fails(client: AsyncClient, auth_headers):
    """Reopening an account that is not closed returns 400."""
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Not Closed", "type": "checking", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    reopen_resp = await client.post(f"/api/accounts/{account_id}/reopen", headers=auth_headers)
    assert reopen_resp.status_code == 400


@pytest.mark.asyncio
async def test_closed_accounts_excluded_from_list(client: AsyncClient, auth_headers):
    """Closed accounts are excluded from the default account listing."""
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Will Close", "type": "checking", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    # Close it
    await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)

    # Default list should not include it
    list_resp = await client.get("/api/accounts", headers=auth_headers)
    ids = [a["id"] for a in list_resp.json()]
    assert account_id not in ids


@pytest.mark.asyncio
async def test_closed_accounts_included_when_requested(client: AsyncClient, auth_headers):
    """Closed accounts appear when include_closed=true."""
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Closed Visible", "type": "checking", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)

    # With include_closed=true
    list_resp = await client.get(
        "/api/accounts", params={"include_closed": "true"}, headers=auth_headers
    )
    ids = [a["id"] for a in list_resp.json()]
    assert account_id in ids


@pytest.mark.asyncio
async def test_close_account_not_found(client: AsyncClient, auth_headers, clean_db):
    """Closing a nonexistent account returns 404."""
    resp = await client.post(
        "/api/accounts/00000000-0000-0000-0000-000000000000/close",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_closed_account_excluded_from_dashboard_balance(client: AsyncClient, auth_headers):
    """Dashboard total balance should exclude closed accounts."""
    # Create account with balance
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Dashboard Test", "type": "checking", "balance": "5000.00", "currency": "BRL"},
    )
    account_id = resp.json()["id"]

    # Check balance is included
    summary_resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    total_before = sum(float(v) for v in summary_resp.json()["total_balance"].values())
    assert total_before == 5000.0

    # Close account
    await client.post(f"/api/accounts/{account_id}/close", headers=auth_headers)

    # Balance should now be excluded
    summary_resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    total_after = sum(float(v) for v in summary_resp.json()["total_balance"].values())
    assert total_after == 0.0


@pytest.mark.asyncio
async def test_archive_can_keep_or_exclude_history_and_reopen_restores_it(
    client: AsyncClient, auth_headers
):
    """The history policy is explicit, reversible, and never deletes rows."""
    account_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Old Checking", "type": "checking", "currency": "BRL"},
    )
    account_id = account_resp.json()["id"]
    transaction_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account_id,
            "description": "Old account expense",
            "amount": "250.00",
            "date": "2026-01-10",
            "type": "debit",
        },
    )
    assert transaction_resp.status_code == 201
    transaction_id = transaction_resp.json()["id"]

    kept = await client.post(
        f"/api/accounts/{account_id}/close",
        headers=auth_headers,
    )
    assert kept.status_code == 200
    assert kept.json()["exclude_from_history"] is False

    history = await client.get("/api/transactions", headers=auth_headers)
    assert transaction_id in {item["id"] for item in history.json()["items"]}

    reopened = await client.post(
        f"/api/accounts/{account_id}/reopen",
        headers=auth_headers,
    )
    assert reopened.json()["exclude_from_history"] is False

    excluded = await client.post(
        f"/api/accounts/{account_id}/close",
        params={"exclude_history": "true"},
        headers=auth_headers,
    )
    assert excluded.status_code == 200
    assert excluded.json()["exclude_from_history"] is True

    excluded_history = await client.get("/api/transactions", headers=auth_headers)
    assert transaction_id not in {item["id"] for item in excluded_history.json()["items"]}

    audit_history = await client.get(
        "/api/transactions",
        params={"include_excluded_history": "true"},
        headers=auth_headers,
    )
    assert transaction_id in {item["id"] for item in audit_history.json()["items"]}

    await client.post(f"/api/accounts/{account_id}/reopen", headers=auth_headers)
    restored_history = await client.get("/api/transactions", headers=auth_headers)
    assert transaction_id in {item["id"] for item in restored_history.json()["items"]}
