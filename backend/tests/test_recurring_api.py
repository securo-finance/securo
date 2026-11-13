"""Tests for recurring transactions API."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace


@pytest.fixture(autouse=True)
def frozen_recent_calendar(monkeypatch):
    monkeypatch.setattr("app.services.recurring_price_change_service.app_today",
                        lambda: date(2026, 7, 15))


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
async def test_generate_pending_button_covers_auto_generate_off(client, auth_headers, test_account):
    """The endpoint behind Generate Pending also creates schedules whose
    auto-generate toggle is off (issue #1145)."""
    create_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Manual rent",
            "amount": 900.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-02-01",
            "account_id": str(test_account.id),
            "auto_generate": False,
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["auto_generate"] is False

    gen_resp = await client.post(
        "/api/recurring-transactions/generate",
        headers=auth_headers,
    )
    assert gen_resp.status_code == 200
    assert gen_resp.json()["generated"] >= 1


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


# ---------------------------------------------------------------------------
# Read-only price-change routes
# ---------------------------------------------------------------------------


def _add_months(when: date, months: int) -> date:
    import calendar

    month_index = when.month - 1 + months
    year = when.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(when.day, calendar.monthrange(year, month)[1]))


async def _seed_price_series(
    session,
    *,
    workspace,
    user,
    account,
    description,
    start,
    amounts=("10.00", "10.00", "11.00"),
):
    """Persist a debit BRL series with posted csv rows, one per amount."""
    series = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description=description,
        amount=Decimal(amounts[0]),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=start,
        next_occurrence=_add_months(start, len(amounts)),
    )
    session.add(series)
    rows = []
    for index, amount in enumerate(amounts):
        rows.append(
            Transaction(
                id=uuid.uuid4(),
                user_id=user.id,
                workspace_id=workspace.id,
                account_id=account.id,
                description=description,
                amount=Decimal(amount),
                currency="BRL",
                # Keep query fixtures in the frozen Recent window, retaining order.
                date=date(2026, 7, 1) + timedelta(
                    days=(start - date(2026, 1, 1)).days // 30 + index
                ),
                type="debit",
                source="csv",
                status="posted",
                recurring_transaction_id=series.id,
            )
        )
    session.add_all(rows)
    await session.commit()
    return series, rows


@pytest.mark.asyncio
async def test_recent_price_changes_returns_latest_event_per_series(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    first, _ = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Streaming",
        start=date(2026, 1, 15),
        amounts=("10.00", "10.00", "10.00", "12.00", "12.00", "12.00", "15.00"),
    )
    second, _ = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Academia",
        start=date(2026, 2, 1),
        amounts=("20.00", "20.00", "25.00"),
    )

    response = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers
    )

    assert response.status_code == 200
    events = response.json()
    # The two-event series appears exactly once, carrying only its latest event.
    first_events = [event for event in events if event["recurring_id"] == str(first.id)]
    assert len(first_events) == 1
    assert first_events[0]["previous_amount"] == "12.00"
    assert first_events[0]["current_amount"] == "15.00"
    second_events = [event for event in events if event["recurring_id"] == str(second.id)]
    assert len(second_events) == 1
    assert second_events[0]["current_amount"] == "25.00"
    # Newest current_date first; ISO dates sort chronologically as strings.
    dates = [event["current_date"] for event in events]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amounts, expected",
    [
        (("39.90", "39.90", "42.90"), ("39.90", "42.90", "3.00", "7.52")),
        (("17.00", "17.00", "15.00"), ("17.00", "15.00", "2.00", "-11.76")),
    ],
)
async def test_both_price_change_routes_preserve_exact_decimal_strings(
    client, auth_headers, session, test_user, test_workspace, test_account,
    amounts, expected,
):
    series, _ = await _seed_price_series(
        session, workspace=test_workspace, user=test_user, account=test_account,
        description="Wire contract", start=date(2026, 1, 15), amounts=amounts,
    )
    fields = ("previous_amount", "current_amount", "absolute_delta", "percent_delta")
    for path in (
        f"/api/recurring-transactions/{series.id}/price-changes",
        "/api/recurring-transactions/price-changes/recent",
    ):
        response = await client.get(path, headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        events = body["changes"] if isinstance(body, dict) else body
        assert len(events) == 1
        assert {field: events[0][field] for field in fields} == dict(zip(fields, expected))


@pytest.mark.asyncio
@pytest.mark.parametrize("amounts", [
    ("10.00", "10.00", "10.00"),
    ("80.00", "100.00", "90.00", "110.00"),
])
async def test_price_history_no_qualifying_change_contract(
    client, auth_headers, session, test_user, test_workspace, test_account, amounts,
):
    series, _ = await _seed_price_series(
        session, workspace=test_workspace, user=test_user, account=test_account,
        description="No qualifying change", start=date(2026, 1, 15), amounts=amounts,
    )
    response = await client.get(
        f"/api/recurring-transactions/{series.id}/price-changes", headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["eligibility"] == "no_qualifying_change"
    assert response.json()["eligible_occurrence_count"] == len(amounts)
    assert response.json()["changes"] == []


@pytest.mark.asyncio
async def test_price_history_is_scoped_to_workspace(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    other = Workspace(
        id=uuid.uuid4(),
        name="Outro",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
        locale="en",
    )
    session.add(other)
    await session.commit()
    foreign, _ = await _seed_price_series(
        session,
        workspace=other,
        user=test_user,
        account=test_account,
        description="Netflix FR",
        start=date(2026, 1, 1),
    )
    ws_headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    # A series in another workspace is unknown from this one...
    response = await client.get(
        f"/api/recurring-transactions/{foreign.id}/price-changes", headers=ws_headers
    )
    assert response.status_code == 404

    # ...and stays unknown even when the other workspace is named directly,
    # because the caller is not a member of it.
    other_headers = {**auth_headers, "X-Workspace-Id": str(other.id)}
    response = await client.get(
        f"/api/recurring-transactions/{foreign.id}/price-changes", headers=other_headers
    )
    assert response.status_code == 404

    # A linked row physically stored in another workspace cannot leak into
    # this workspace's history even when the FK points at an in-scope series.
    own, _ = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Spotify",
        start=date(2026, 1, 10),
        amounts=("10.00", "10.00", "10.00"),
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=other.id,
            account_id=test_account.id,
            description="Spotify",
            amount=Decimal("11.00"),
            currency="BRL",
            date=_add_months(date(2026, 1, 10), 3),
            type="debit",
            source="csv",
            status="posted",
            recurring_transaction_id=own.id,
        )
    )
    await session.commit()

    response = await client.get(
        f"/api/recurring-transactions/{own.id}/price-changes", headers=ws_headers
    )
    assert response.status_code == 200
    body = response.json()
    # Only the three same-workspace rows count; the foreign 11.00 cannot
    # complete an event.
    assert body["eligible_occurrence_count"] == 3
    assert body["changes"] == []

    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=ws_headers
    )
    assert recent.status_code == 200
    assert all(event["recurring_id"] != str(foreign.id) for event in recent.json())


@pytest.mark.asyncio
async def test_price_history_unknown_series_returns_404(client, auth_headers):
    response = await client.get(
        f"/api/recurring-transactions/{uuid.uuid4()}/price-changes", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Recurring transaction not found"


@pytest.mark.asyncio
async def test_price_history_does_not_mutate_series_or_transactions(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    series, rows = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Netflix",
        start=date(2026, 1, 15),
    )
    row_ids = [row.id for row in rows]

    async def snapshot():
        tx_result = await session.execute(
            select(Transaction)
            .where(Transaction.id.in_(row_ids))
            .execution_options(populate_existing=True)
        )
        txs = sorted(tx_result.scalars().all(), key=lambda tx: tx.date)
        series_result = await session.execute(
            select(RecurringTransaction)
            .where(RecurringTransaction.id == series.id)
            .execution_options(populate_existing=True)
        )
        stored = series_result.scalar_one()
        return (
            len(txs),
            tuple(
                (tx.id, tx.recurring_transaction_id, tx.amount, tx.status, tx.source)
                for tx in txs
            ),
            stored.amount,
            stored.next_occurrence,
            stored.currency,
        )

    before = await snapshot()
    history = await client.get(
        f"/api/recurring-transactions/{series.id}/price-changes", headers=auth_headers
    )
    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers
    )
    assert history.status_code == 200
    assert recent.status_code == 200
    assert len(history.json()["changes"]) == 1
    assert len(recent.json()) == 1
    assert await snapshot() == before


@pytest.mark.asyncio
async def test_price_history_ignores_unlinked_transactions(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    series, _ = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Netflix",
        start=date(2026, 1, 15),
        amounts=("10.00", "10.00", "11.00"),
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="GHOST CHARGE",
            amount=Decimal("99.00"),
            currency="BRL",
            date=_add_months(date(2026, 1, 15), 3),
            type="debit",
            source="csv",
            status="posted",
            # No recurring_transaction_id: unlinked, therefore invisible.
        )
    )
    await session.commit()

    history = await client.get(
        f"/api/recurring-transactions/{series.id}/price-changes", headers=auth_headers
    )
    assert history.status_code == 200
    body = history.json()
    assert body["eligible_occurrence_count"] == 3
    assert len(body["changes"]) == 1
    assert body["changes"][0]["current_amount"] == "11.00"

    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers
    )
    assert recent.status_code == 200
    assert len(recent.json()) == 1
    assert recent.json()[0]["current_amount"] == "11.00"


@pytest.mark.asyncio
async def test_price_history_route_is_readable_by_read_workspace(
    client, viewer_auth_headers, session, test_user, test_workspace, test_account
):
    series, _ = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Netflix",
        start=date(2026, 1, 15),
    )

    history = await client.get(
        f"/api/recurring-transactions/{series.id}/price-changes",
        headers=viewer_auth_headers,
    )
    assert history.status_code == 200
    assert history.json()["eligibility"] == "changes_detected"

    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=viewer_auth_headers
    )
    assert recent.status_code == 200
    assert len(recent.json()) == 1

    # The projection is read-only for a read role: no write route opens up,
    # and the UI hides its mutation controls for the same viewer.
    create = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Should fail",
            "amount": 10.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": "2026-03-01",
            "account_id": str(test_account.id),
        },
        headers=viewer_auth_headers,
    )
    assert create.status_code == 403


@pytest.mark.asyncio
async def test_recent_price_changes_limit_is_bounded(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    for index in range(21):
        await _seed_price_series(
            session,
            workspace=test_workspace,
            user=test_user,
            account=test_account,
            description=f"Servico {index}",
            start=date(2026, 1, 1) + timedelta(days=index),
        )

    default_response = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers
    )
    assert default_response.status_code == 200
    assert len(default_response.json()) == 20  # default limit

    one = await client.get(
        "/api/recurring-transactions/price-changes/recent?limit=1", headers=auth_headers
    )
    assert one.status_code == 200
    assert len(one.json()) == 1

    fifty = await client.get(
        "/api/recurring-transactions/price-changes/recent?limit=50", headers=auth_headers
    )
    assert fifty.status_code == 200
    assert len(fifty.json()) == 21  # all series, within the bound

    for invalid in ("0", "51", "abc"):
        rejected = await client.get(
            f"/api/recurring-transactions/price-changes/recent?limit={invalid}",
            headers=auth_headers,
        )
        assert rejected.status_code == 422, invalid


@pytest.mark.asyncio
async def test_price_history_reuses_real_source_allowlist(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    series, rows = await _seed_price_series(
        session,
        workspace=test_workspace,
        user=test_user,
        account=test_account,
        description="Condomínio",
        start=date(2026, 1, 15),
        amounts=("10.00", "10.00", "10.00"),
    )
    # Synthetic rows at a new amount would complete a price change if the
    # real-source allowlist leaked.
    for offset, source in ((3, "opening_balance"), (4, "recurring")):
        session.add(
            Transaction(
                id=uuid.uuid4(),
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                account_id=test_account.id,
                description=series.description,
                amount=Decimal("12.00"),
                currency="BRL",
                date=_add_months(date(2026, 1, 15), offset),
                type="debit",
                source=source,
                status="posted",
                recurring_transaction_id=series.id,
            )
        )
    await session.commit()

    history = await client.get(
        f"/api/recurring-transactions/{series.id}/price-changes", headers=auth_headers
    )
    assert history.status_code == 200
    body = history.json()
    assert body["eligible_occurrence_count"] == 3
    assert body["changes"] == []

    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers
    )
    assert recent.status_code == 200
    assert recent.json() == []
