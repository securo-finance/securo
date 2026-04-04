"""Tests for dashboard API endpoints."""
import calendar
from datetime import date, timedelta

import pytest
from sqlalchemy import update

from app.models.monthly_snapshot import MonthlySnapshot
from app.models.user import User


def _current_month_str() -> str:
    """Return the 1st of the current month as 'YYYY-MM-DD'."""
    return date.today().replace(day=1).isoformat()


def _prev_month_str() -> str:
    """Return the 1st of the previous month as 'YYYY-MM-DD'."""
    first = date.today().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    return prev.isoformat()


def _next_month_str() -> str:
    """Return the 1st of next month as 'YYYY-MM-DD'."""
    first = date.today().replace(day=1)
    if first.month == 12:
        return first.replace(year=first.year + 1, month=1).isoformat()
    return first.replace(month=first.month + 1).isoformat()


def _future_month_str(months_ahead: int) -> str:
    """Return the 1st of N months ahead as 'YYYY-MM-DD'."""
    d = date.today().replace(day=1)
    for _ in range(months_ahead):
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1)
        else:
            d = d.replace(month=d.month + 1)
    return d.isoformat()


@pytest.mark.asyncio
async def test_spending_by_category(client, auth_headers, test_transactions):
    """Spending by category returns numeric totals (not strings) for chart rendering."""
    response = await client.get("/api/dashboard/spending-by-category", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0

    for item in data:
        assert "category_name" in item
        assert "total" in item
        assert "percentage" in item
        # total MUST be a number (float/int), not a string — Recharts requires this
        assert isinstance(item["total"], (int, float)), (
            f"total should be numeric, got {type(item['total']).__name__}: {item['total']}"
        )
        assert isinstance(item["percentage"], (int, float))
        assert item["total"] > 0


@pytest.mark.asyncio
async def test_spending_by_category_with_month(client, auth_headers, test_transactions):
    """Spending by category filters by month correctly."""
    # Current month — when test transactions exist
    response = await client.get(
        "/api/dashboard/spending-by-category",
        params={"month": _current_month_str()},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0

    # Previous month — no test transactions
    response = await client.get(
        "/api/dashboard/spending-by-category",
        params={"month": _prev_month_str()},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


@pytest.mark.asyncio
async def test_summary_numeric_fields(client, auth_headers, test_transactions):
    """Summary endpoint returns numeric values, not strings."""
    response = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data["monthly_income"], (int, float))
    assert isinstance(data["monthly_expenses"], (int, float))
    for currency, balance in data["total_balance"].items():
        assert isinstance(balance, (int, float)), (
            f"total_balance[{currency}] should be numeric, got {type(balance).__name__}"
        )


@pytest.mark.asyncio
async def test_dashboard_virtual_recurring_projection(
    client, auth_headers, test_categories
):
    """Dashboard includes recurring projections via virtual computation — no DB writes."""
    # Use a future month so projections always apply regardless of today's date.
    next_month = _next_month_str()
    next_month_date = date.fromisoformat(next_month)

    # Create a weekly recurring starting on the 1st of next month
    rec_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Weekly groceries",
            "amount": 50.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "weekly",
            "start_date": next_month,
            "category_id": str(test_categories[0].id),
        },
        headers=auth_headers,
    )
    assert rec_resp.status_code == 201

    # Query dashboard spending for that future month
    spending_resp = await client.get(
        "/api/dashboard/spending-by-category",
        params={"month": next_month},
        headers=auth_headers,
    )
    assert spending_resp.status_code == 200
    spending = spending_resp.json()
    assert len(spending) > 0

    cat_spending = next(
        (s for s in spending if s["category_id"] == str(test_categories[0].id)), None
    )
    assert cat_spending is not None
    # At least 4 weekly occurrences in a month = at least 200
    assert cat_spending["total"] >= 200.0

    # CRITICAL: No transactions should have been created in the DB (virtual projection)
    # Compute the last day of next month for the query range
    if next_month_date.month == 12:
        month_end = next_month_date.replace(year=next_month_date.year + 1, month=1)
    else:
        month_end = next_month_date.replace(month=next_month_date.month + 1)
    tx_resp = await client.get(
        "/api/transactions",
        params={"from": next_month, "to": (month_end - timedelta(days=1)).isoformat()},
        headers=auth_headers,
    )
    assert tx_resp.status_code == 200
    groceries = [t for t in tx_resp.json()["items"] if t["description"] == "Weekly groceries"]
    assert len(groceries) == 0, "Virtual projections must NOT create DB records"

    # Summary should also include the projected expenses
    summary_resp = await client.get(
        "/api/dashboard/summary",
        params={"month": next_month},
        headers=auth_headers,
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json()["monthly_expenses"] >= 200.0


@pytest.mark.asyncio
async def test_dashboard_no_duplicates_on_concurrent_calls(
    client, auth_headers, test_categories
):
    """Calling summary and spending concurrently does NOT create duplicates."""
    import asyncio

    next_month = _next_month_str()
    next_month_date = date.fromisoformat(next_month)

    # Create a monthly recurring starting next month
    await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Rent",
            "amount": 1500.00,
            "type": "debit",
            "frequency": "monthly",
            "start_date": next_month,
            "category_id": str(test_categories[1].id),
        },
        headers=auth_headers,
    )

    # Call both dashboard endpoints in parallel (simulating frontend)
    summary_coro = client.get(
        "/api/dashboard/summary", params={"month": next_month}, headers=auth_headers
    )
    spending_coro = client.get(
        "/api/dashboard/spending-by-category", params={"month": next_month}, headers=auth_headers
    )
    summary_resp, spending_resp = await asyncio.gather(summary_coro, spending_coro)

    assert summary_resp.status_code == 200
    assert spending_resp.status_code == 200

    # Call them again — results should be identical (no duplicate accumulation)
    summary2 = await client.get(
        "/api/dashboard/summary", params={"month": next_month}, headers=auth_headers
    )
    spending2 = await client.get(
        "/api/dashboard/spending-by-category", params={"month": next_month}, headers=auth_headers
    )

    assert summary_resp.json()["monthly_expenses"] == summary2.json()["monthly_expenses"]
    assert spending_resp.json() == spending2.json()

    # No transaction records should exist — projections are virtual
    if next_month_date.month == 12:
        month_end = next_month_date.replace(year=next_month_date.year + 1, month=1)
    else:
        month_end = next_month_date.replace(month=next_month_date.month + 1)
    tx_resp = await client.get(
        "/api/transactions",
        params={"from": next_month, "to": (month_end - timedelta(days=1)).isoformat()},
        headers=auth_headers,
    )
    rent_txs = [t for t in tx_resp.json()["items"] if t["description"] == "Rent"]
    assert len(rent_txs) == 0, "Virtual projections must NOT create DB records"


@pytest.mark.asyncio
async def test_recurring_projection_respects_end_date(
    client, auth_headers, test_categories
):
    """Recurring with end_date stops projecting after that date."""
    next_month = _next_month_str()
    next_month_date = date.fromisoformat(next_month)
    end_date = next_month_date.replace(day=15).isoformat()

    await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Temp sub",
            "amount": 20.00,
            "type": "debit",
            "frequency": "weekly",
            "start_date": next_month,
            "end_date": end_date,
            "category_id": str(test_categories[0].id),
        },
        headers=auth_headers,
    )

    # Next month spending should include only 3 occurrences: 1st, 8th, 15th = 60
    spending_resp = await client.get(
        "/api/dashboard/spending-by-category",
        params={"month": next_month},
        headers=auth_headers,
    )
    cat_spending = next(
        (s for s in spending_resp.json() if s["category_id"] == str(test_categories[0].id)), None
    )
    assert cat_spending is not None
    assert cat_spending["total"] == 60.0


@pytest.mark.asyncio
async def test_summary_defaults_to_selected_snapshot_context(
    client, auth_headers, test_transactions, test_user, test_monthly_period, session
):
    current_period = (test_user.preferences or {})["current_month_period"]
    next_period = _future_month_str(1)[:7]
    session.add(
        MonthlySnapshot(
            user_id=test_user.id,
            monthly_period_id=test_monthly_period.id,
            period=current_period,
        )
    )
    await session.commit()

    await session.execute(
        update(User)
        .where(User.id == test_user.id)
        .values(
            preferences={
                **(test_user.preferences or {}),
                "current_month_period": next_period,
                "selected_snapshot_period": current_period,
            }
        )
    )
    await session.commit()

    response = await client.get("/api/dashboard/summary", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["monthly_income"] == pytest.approx(8150.0)
    assert data["monthly_expenses"] == pytest.approx(110.4)


@pytest.mark.asyncio
@pytest.mark.skip(reason="to_char() is PostgreSQL-specific; tests use SQLite")
async def test_monthly_trend_numeric_fields(client, auth_headers, test_transactions):
    """Monthly trend returns numeric values, not strings."""
    response = await client.get("/api/dashboard/monthly-trend", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0

    for item in data:
        assert isinstance(item["income"], (int, float))
        assert isinstance(item["expenses"], (int, float))


@pytest.mark.asyncio
async def test_opening_balance_not_counted_as_monthly_income(client, auth_headers):
    """Opening balance transactions must NOT inflate monthly_income on dashboard."""
    # Create a manual account with a 5000 opening balance (this month)
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "Savings", "type": "savings", "balance": 5000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201

    # Dashboard for current month must NOT count the 5000 as income
    resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["monthly_income"] == 0.0, (
        f"Opening balance must not appear as income; got {data['monthly_income']}"
    )
    # But total_balance should reflect it
    total = sum(float(v) for v in data["total_balance"].values())
    assert total == 5000.0, f"Total balance should include opening balance; got {total}"


@pytest.mark.asyncio
async def test_accounts_count_includes_manual_accounts(client, auth_headers):
    """accounts_count must include manual (non-bank-connected) accounts."""
    # Create two manual accounts
    for name in ("Wallet", "Piggy Bank"):
        resp = await client.post(
            "/api/accounts",
            json={"name": name, "type": "checking", "balance": 0, "currency": "BRL"},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accounts_count"] >= 2, (
        f"Expected ≥2 accounts, got {data['accounts_count']}"
    )


@pytest.mark.asyncio
async def test_total_balance_computed_from_transactions(client, auth_headers):
    """Total balance is the signed sum of all transactions, not the stale Account.balance field."""
    # Create a manual account with 1000 opening balance
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "Current Account", "type": "checking", "balance": 1000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201
    acc_id = acc_resp.json()["id"]

    # Add a 200 debit — balance should drop to 800
    await client.post(
        "/api/transactions",
        json={
            "description": "Groceries",
            "amount": 200.00,
            "currency": "BRL",
            "type": "debit",
            "date": "2026-02-20",
            "account_id": acc_id,
        },
        headers=auth_headers,
    )

    resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    data = resp.json()
    total = sum(float(v) for v in data["total_balance"].values())
    assert total == 800.0, f"Expected 800.0 (1000 - 200), got {total}"


@pytest.mark.asyncio
async def test_future_month_balance_includes_recurring_projections(
    client, auth_headers, test_categories
):
    """When viewing a future month, total_balance must reflect projected recurring cash flows."""
    next_month = _next_month_str()
    month_after = _future_month_str(2)


    # Create an account with 2000 starting balance
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "Main Account", "type": "checking", "balance": 2000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201

    # Create a monthly recurring expense of 500 starting next month
    rec_resp = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Rent",
            "amount": 500.00,
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": next_month,
            "category_id": str(test_categories[1].id),
        },
        headers=auth_headers,
    )
    assert rec_resp.status_code == 201

    # Current month balance: should still be 2000 (recurring starts next month)
    current_resp = await client.get(
        "/api/dashboard/summary", params={"month": _current_month_str()}, headers=auth_headers
    )
    assert current_resp.status_code == 200
    base_total = sum(float(v) for v in current_resp.json()["total_balance"].values())

    # Next month: 2000 - 500 (projected rent) = 1500
    next_resp = await client.get(
        "/api/dashboard/summary", params={"month": next_month}, headers=auth_headers
    )
    assert next_resp.status_code == 200
    next_total = sum(float(v) for v in next_resp.json()["total_balance"].values())
    assert next_total == base_total - 500.0, (
        f"Next month balance should be 500 less; got {next_total} vs {base_total}"
    )

    # Month after: 2000 - 500 - 500 = 1000
    after_resp = await client.get(
        "/api/dashboard/summary", params={"month": month_after}, headers=auth_headers
    )
    assert after_resp.status_code == 200
    after_total = sum(float(v) for v in after_resp.json()["total_balance"].values())
    assert after_total == base_total - 1000.0, (
        f"Month+2 balance should be 1000 less; got {after_total} vs {base_total}"
    )


@pytest.mark.asyncio
async def test_past_month_balance_filtered_by_end_of_month(client, auth_headers):
    """Past month balance only includes transactions up to the end of that month."""
    today = date.today()
    # Pick a month that's definitely in the past
    past_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    past_last_day = calendar.monthrange(past_first.year, past_first.month)[1]
    past_last = past_first.replace(day=past_last_day)
    past_month_param = past_first.isoformat()

    # Create an account with 1000 opening balance (today — after the past month)
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "TimeTest", "type": "checking", "balance": 1000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201
    acc_id = acc_resp.json()["id"]

    # Add a transaction in the past month
    await client.post(
        "/api/transactions",
        json={
            "description": "Past expense",
            "amount": 200.00,
            "currency": "BRL",
            "type": "debit",
            "date": past_first.replace(day=15).isoformat(),
            "account_id": acc_id,
        },
        headers=auth_headers,
    )

    # Query summary for past month — balance should NOT include the opening_balance
    # transaction (dated today) but SHOULD include the past month transaction.
    resp = await client.get(
        "/api/dashboard/summary",
        params={"month": past_month_param},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    total = sum(float(v) for v in data["total_balance"].values())
    # Only the past debit should be counted (opening balance is dated today)
    assert total == -200.0, f"Expected -200.0 for past month, got {total}"
    assert data["balance_date"] == past_last.isoformat()


@pytest.mark.asyncio
async def test_current_month_balance_uses_today_as_cutoff(client, auth_headers):
    """Current month balance uses today as the cutoff date."""
    today = date.today()

    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "TodayTest", "type": "checking", "balance": 500.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201

    resp = await client.get(
        "/api/dashboard/summary",
        params={"month": today.replace(day=1).isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance_date"] == today.isoformat()


@pytest.mark.asyncio
async def test_balance_date_parameter_overrides_default_cutoff(client, auth_headers):
    """Providing balance_date overrides the default cutoff calculation."""
    today = date.today()

    # Create account with zero balance (no opening_balance transaction)
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "OverrideTest", "type": "checking", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201
    acc_id = acc_resp.json()["id"]

    # Add a credit dated 15 days ago
    fifteen_days_ago = (today - timedelta(days=15)).isoformat()
    await client.post(
        "/api/transactions",
        json={
            "description": "Salary",
            "amount": 1000.00,
            "currency": "BRL",
            "type": "credit",
            "date": fifteen_days_ago,
            "account_id": acc_id,
        },
        headers=auth_headers,
    )

    # Add a debit dated 5 days ago
    five_days_ago = (today - timedelta(days=5)).isoformat()
    await client.post(
        "/api/transactions",
        json={
            "description": "Recent purchase",
            "amount": 300.00,
            "currency": "BRL",
            "type": "debit",
            "date": five_days_ago,
            "account_id": acc_id,
        },
        headers=auth_headers,
    )

    # Query with balance_date = 10 days ago (after credit, before debit)
    ten_days_ago = (today - timedelta(days=10)).isoformat()
    resp = await client.get(
        "/api/dashboard/summary",
        params={
            "month": today.replace(day=1).isoformat(),
            "balance_date": ten_days_ago,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    total = sum(float(v) for v in data["total_balance"].values())
    # Should only include the credit (1000), not the debit from 5 days ago
    assert total == 1000.0, f"Expected 1000.0 with cutoff 10 days ago, got {total}"
    assert data["balance_date"] == ten_days_ago

    # Now query without balance_date (default cutoff = today) — should include both
    resp2 = await client.get(
        "/api/dashboard/summary",
        params={"month": today.replace(day=1).isoformat()},
        headers=auth_headers,
    )
    total2 = sum(float(v) for v in resp2.json()["total_balance"].values())
    assert total2 == 700.0, f"Expected 700.0 with default cutoff, got {total2}"
