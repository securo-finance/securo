"""Tests for recurring transactions API."""
from datetime import date, timedelta

import pytest


@pytest.mark.asyncio
async def test_create_recurring_transaction(client, auth_headers, test_categories):
    """Creating a recurring transaction sets next_occurrence to start_date."""
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Netflix",
            "amount": 39.90,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-03-01",
            "category_id": str(test_categories[0].id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Netflix"
    assert data["frequency"] == "monthly"
    assert data["is_active"] is True
    # next_occurrence should be start_date since skip_first not set
    assert data["next_occurrence"] == "2026-03-01"


@pytest.mark.asyncio
async def test_create_recurring_with_skip_first(client, auth_headers, test_categories):
    """skip_first=true advances next_occurrence by one frequency period."""
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Mercadinho",
            "amount": 50.00,
            "currency": "EUR",
            "type": "debit",
            "frequency": "weekly",
            "start_date": "2026-02-25",
            "skip_first": True,
            "category_id": str(test_categories[0].id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Mercadinho"
    assert data["frequency"] == "weekly"
    # next_occurrence should be one week ahead (not start_date)
    assert data["next_occurrence"] == "2026-03-04"


@pytest.mark.asyncio
async def test_create_recurring_skip_first_monthly(client, auth_headers):
    """skip_first with monthly frequency advances by one month."""
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Rent",
            "amount": 1500.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-01-15",
            "skip_first": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["next_occurrence"] == "2026-02-15"


@pytest.mark.asyncio
async def test_generate_pending_creates_transactions(client, auth_headers, test_account):
    """Generate pending creates transactions and advances next_occurrence."""
    # Create a recurring that's already past due
    create_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Weekly gym",
            "amount": 30.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "weekly",
            "start_date": "2026-02-01",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    rec_id = create_resp.json()["id"]

    # Generate pending — should create multiple transactions up to today
    gen_resp = await client.post(
        "/api/recurring-transactions/generate",
        headers=auth_headers,
    )
    assert gen_resp.status_code == 200
    generated = gen_resp.json()["generated"]
    assert generated >= 1

    # Verify the recurring's next_occurrence is now in the future
    await client.get(
        f"/api/recurring-transactions/{rec_id}",
        headers=auth_headers,
    )
    # It may be 404 if list-only, check the list
    list_resp = await client.get(
        "/api/recurring-transactions",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    rec = next(r for r in list_resp.json() if r["id"] == rec_id)
    # next_occurrence should be after today (2026-02-25)
    assert rec["next_occurrence"] > "2026-02-25"


@pytest.mark.asyncio
async def test_generate_no_duplicate_with_skip_first(client, auth_headers, test_categories, test_account):
    """When created with skip_first, generate doesn't create duplicate for start_date."""
    # Use relative dates so the test doesn't go stale over time
    today = date.today()
    start_date = (today - timedelta(days=10)).isoformat()
    next_day = (today - timedelta(days=9)).isoformat()

    # Simulate creating a transaction + recurring with skip_first (as the UI does)
    # First, create the transaction
    tx_resp = await client.post(
        "/api/transactions",
        json={
            "description": "Internet bill",
            "amount": 99.90,
            "currency": "BRL",
            "type": "debit",
            "date": start_date,
            "category_id": str(test_categories[1].id),
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert tx_resp.status_code == 201

    # Then create the recurring with skip_first=true
    rec_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Internet bill",
            "amount": 99.90,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": start_date,
            "skip_first": True,
            "category_id": str(test_categories[1].id),
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert rec_resp.status_code == 201
    # next_occurrence should be one month after start_date (in the future)
    assert rec_resp.json()["next_occurrence"] > today.isoformat()

    # Generate pending — should NOT create anything (next_occurrence is in the future)
    gen_resp = await client.post(
        "/api/recurring-transactions/generate",
        headers=auth_headers,
    )
    assert gen_resp.status_code == 200
    assert gen_resp.json()["generated"] == 0

    # Verify only one "Internet bill" transaction exists for start_date
    tx_list = await client.get(
        "/api/transactions",
        params={"from": start_date, "to": next_day},
        headers=auth_headers,
    )
    assert tx_list.status_code == 200
    internet_txs = [t for t in tx_list.json()["items"] if t["description"] == "Internet bill"]
    assert len(internet_txs) == 1


@pytest.mark.asyncio
async def test_list_recurring_transactions(client, auth_headers):
    """List returns all recurring transactions for the user."""
    # Create two
    for desc in ["Sub A", "Sub B"]:
        await client.post(
            "/api/recurring-transactions",
            json={
                "description": desc,
                "amount": 10.00,
                "type": "debit",
                "frequency": "monthly",
                "start_date": "2026-03-01",
            },
            headers=auth_headers,
        )

    response = await client.get("/api/recurring-transactions", headers=auth_headers)
    assert response.status_code == 200
    descriptions = [r["description"] for r in response.json()]
    assert "Sub A" in descriptions
    assert "Sub B" in descriptions


@pytest.mark.asyncio
async def test_delete_recurring_transaction(client, auth_headers):
    """Delete removes a recurring transaction."""
    create_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "To delete",
            "amount": 5.00,
            "type": "debit",
            "frequency": "weekly",
            "start_date": "2026-03-01",
        },
        headers=auth_headers,
    )
    rec_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/recurring-transactions/{rec_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 204
