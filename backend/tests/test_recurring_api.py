"""Tests for recurring transactions API."""
from datetime import date, timedelta

import pytest


@pytest.mark.asyncio
async def test_create_recurring_transaction(client, auth_headers, test_categories, test_account):
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
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Netflix"
    assert data["frequency"] == "monthly"
    assert data["is_active"] is True
    assert data["weekend_adjustment"] == "none"
    # next_occurrence should be start_date since skip_first not set
    assert data["next_occurrence"] == "2026-03-01"


@pytest.mark.asyncio
async def test_create_recurring_with_skip_first(client, auth_headers, test_categories, test_account):
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
            "account_id": str(test_account.id),
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
async def test_create_recurring_skip_first_monthly(client, auth_headers, test_account):
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
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["next_occurrence"] == "2026-02-15"


@pytest.mark.asyncio
async def test_create_recurring_skip_first_quarterly(client, auth_headers, test_account):
    """Quarterly is accepted by the API and skip_first advances three calendar months."""
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Insurance",
            "amount": 300.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "quarterly",
            "day_of_month": 31,
            "start_date": "2026-01-31",
            "skip_first": True,
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["frequency"] == "quarterly"
    assert data["next_occurrence"] == "2026-04-30"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frequency", "start_date", "day_of_month", "expected_next"),
    [
        ("biweekly", "2026-01-05", None, "2026-01-19"),
        ("semiannual", "2026-01-31", 31, "2026-07-31"),
    ],
    ids=["biweekly", "semiannual"],
)
async def test_create_recurring_skip_first_new_frequencies(
    client,
    auth_headers,
    test_account,
    frequency,
    start_date,
    day_of_month,
    expected_next,
):
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": f"{frequency} regression",
            "amount": 100.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": frequency,
            "day_of_month": day_of_month,
            "start_date": start_date,
            "skip_first": True,
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["frequency"] == frequency
    assert data["next_occurrence"] == expected_next


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
async def test_list_recurring_transactions(client, auth_headers, test_account):
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
                "account_id": str(test_account.id),
            },
            headers=auth_headers,
        )

    response = await client.get("/api/recurring-transactions", headers=auth_headers)
    assert response.status_code == 200
    descriptions = [r["description"] for r in response.json()]
    assert "Sub A" in descriptions
    assert "Sub B" in descriptions


@pytest.mark.asyncio
async def test_delete_recurring_transaction(client, auth_headers, test_account):
    """Delete removes a recurring transaction."""
    create_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "To delete",
            "amount": 5.00,
            "type": "debit",
            "frequency": "weekly",
            "start_date": "2026-03-01",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    rec_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/recurring-transactions/{rec_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 204


# ---------------------------------------------------------------------------
# Regression tests for #64 — account_id is required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_recurring_without_account_rejected(client, auth_headers):
    """Omitting account_id should return a validation error, not a 500 later."""
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "No account",
            "amount": 10.00,
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-03-01",
        },
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_pending_skips_legacy_null_account(
    session, test_user, test_account
):
    """Legacy rows with account_id=None (predating the schema fix) must not crash
    generate_pending — they are skipped instead."""
    import uuid as _uuid
    from datetime import date as _date
    from decimal import Decimal as _Decimal
    from app.models.recurring_transaction import RecurringTransaction
    from app.services.recurring_transaction_service import generate_pending

    # Bypass the schema by creating the row directly.
    legacy = RecurringTransaction(
        id=_uuid.uuid4(),
        user_id=test_user.id,
        account_id=None,
        description="Legacy",
        amount=_Decimal("10"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=_date(2026, 1, 1),
        next_occurrence=_date(2026, 1, 1),
        is_active=True,
    )
    session.add(legacy)
    await session.commit()

    # Should not raise; should report 0 generated for the legacy row.
    count = await generate_pending(session, test_user.id, up_to=_date(2026, 4, 1))
    assert count == 0


@pytest.mark.asyncio
async def test_weekend_adjustment_create_read_and_update(
    client, auth_headers, test_account
):
    create_response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Adjusted rent",
            "amount": 1200,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-08-01",
            "weekend_adjustment": "previous_friday",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["weekend_adjustment"] == "previous_friday"
    assert created["next_occurrence"] == "2026-08-01"

    list_response = await client.get(
        "/api/recurring-transactions", headers=auth_headers
    )
    listed = next(row for row in list_response.json() if row["id"] == created["id"])
    assert listed["weekend_adjustment"] == "previous_friday"

    update_response = await client.patch(
        f"/api/recurring-transactions/{created['id']}",
        json={"weekend_adjustment": "next_monday"},
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["weekend_adjustment"] == "next_monday"
    assert updated["next_occurrence"] == "2026-08-01"


@pytest.mark.asyncio
async def test_invalid_weekend_adjustment_is_rejected(
    client, auth_headers, test_account
):
    create_response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Invalid policy",
            "amount": 10,
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-08-01",
            "weekend_adjustment": "nearest_weekday",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert create_response.status_code == 422

    valid_response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Valid policy",
            "amount": 10,
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-08-01",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    recurring_id = valid_response.json()["id"]
    update_response = await client.patch(
        f"/api/recurring-transactions/{recurring_id}",
        json={"weekend_adjustment": "nearest_weekday"},
        headers=auth_headers,
    )
    assert update_response.status_code == 422


@pytest.mark.asyncio
async def test_weekend_adjustment_update_rejects_null_but_allows_omission(
    client, auth_headers, test_account
):
    create_response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Null policy regression",
            "amount": 10,
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-08-01",
            "weekend_adjustment": "previous_friday",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    recurring_id = create_response.json()["id"]

    omitted_response = await client.patch(
        f"/api/recurring-transactions/{recurring_id}",
        json={},
        headers=auth_headers,
    )
    assert omitted_response.status_code == 200
    assert omitted_response.json()["weekend_adjustment"] == "previous_friday"

    null_response = await client.patch(
        f"/api/recurring-transactions/{recurring_id}",
        json={"weekend_adjustment": None},
        headers=auth_headers,
    )
    assert null_response.status_code == 400
    assert null_response.json()["detail"] == "weekend_adjustment is required"

    list_response = await client.get(
        "/api/recurring-transactions", headers=auth_headers
    )
    listed = next(
        row for row in list_response.json() if row["id"] == recurring_id
    )
    assert listed["weekend_adjustment"] == "previous_friday"
