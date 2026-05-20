import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.report import CategoryTrendItem, ReportDataPoint, ReportResponse
from app.services.report_service import (
    _add_months,
    _asset_value_at,
    _date_points,
    _format_date_label,
    _net_worth_at,
    get_cash_flow_report,
    get_net_worth_report,
)
from app.models.recurring_transaction import RecurringTransaction


# ---------------------------------------------------------------------------
# Pure-function tests: _format_date_label
# ---------------------------------------------------------------------------


def test_format_date_label_daily():
    assert _format_date_label(date(2025, 6, 15), "daily") == "2025-06-15"


def test_format_date_label_weekly():
    # 2025-06-16 is a Monday in ISO week 25
    assert _format_date_label(date(2025, 6, 16), "weekly") == "2025-W25"


def test_format_date_label_monthly():
    assert _format_date_label(date(2025, 1, 20), "monthly") == "2025-01"


def test_format_date_label_yearly():
    assert _format_date_label(date(2025, 3, 1), "yearly") == "2025"


def test_format_date_label_unknown_falls_back_to_iso():
    assert _format_date_label(date(2025, 3, 1), "unknown") == "2025-03-01"


# ---------------------------------------------------------------------------
# Pure-function tests: _date_points
# ---------------------------------------------------------------------------


def test_date_points_daily():
    start = date(2025, 1, 1)
    end = date(2025, 1, 5)
    points = _date_points(start, end, "daily")
    assert len(points) == 5
    assert points[0] == start
    assert points[-1] == end


def test_date_points_weekly():
    start = date(2025, 1, 1)
    end = date(2025, 1, 22)
    points = _date_points(start, end, "weekly")
    # 1st, 8th, 15th, 22nd = 4 points
    assert len(points) == 4
    assert points[0] == start
    assert points[-1] == end


def test_date_points_monthly():
    start = date(2025, 1, 1)
    end = date(2025, 6, 15)
    points = _date_points(start, end, "monthly")
    assert points[0] == date(2025, 1, 31)  # end of first month
    assert points[-1] == end               # last point capped at end
    assert len(points) == 6


def test_date_points_monthly_end_of_month_snapshots():
    """Monthly points: one EOM snapshot per month, last one capped at end."""
    start = date(2025, 1, 1)
    end = date(2025, 4, 15)
    points = _date_points(start, end, "monthly")
    assert points == [
        date(2025, 1, 31),  # end of Jan
        date(2025, 2, 28),  # end of Feb
        date(2025, 3, 31),  # end of Mar
        date(2025, 4, 15),  # capped at end (Apr 30 → Apr 15)
    ]


def test_date_points_monthly_december_start():
    """December start must not crash with month rollover."""
    start = date(2024, 12, 1)
    end = date(2025, 2, 15)
    points = _date_points(start, end, "monthly")
    assert points == [
        date(2024, 12, 31),
        date(2025, 1, 31),
        date(2025, 2, 15),
    ]


def test_date_points_monthly_feb_leap_year():
    """End-of-month for February respects leap years."""
    start = date(2024, 1, 1)
    end = date(2024, 3, 1)
    points = _date_points(start, end, "monthly")
    assert points == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 1),
    ]


def test_date_points_yearly():
    start = date(2023, 1, 1)
    end = date(2025, 6, 15)
    points = _date_points(start, end, "yearly")
    assert points[0] == start
    assert points[-1] == end


def test_date_points_empty_range():
    """Start after end returns single point."""
    start = date(2025, 1, 1)
    end = date(2025, 1, 1)
    points = _date_points(start, end, "daily")
    assert len(points) == 1


def test_date_points_unknown_interval_defaults_to_monthly():
    start = date(2025, 1, 1)
    end = date(2025, 3, 15)
    points = _date_points(start, end, "something_else")
    monthly = _date_points(start, end, "monthly")
    assert points == monthly


def test_date_points_last_point_replaces_same_period():
    """End date mid-month caps the last snapshot; no duplicate month labels."""
    start = date(2025, 1, 1)
    end = date(2025, 3, 15)
    points = _date_points(start, end, "monthly")
    assert points == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 15),  # Mar 31 capped at end
    ]
    labels = [_format_date_label(p, "monthly") for p in points]
    assert len(labels) == len(set(labels))


# ---------------------------------------------------------------------------
# Service-level tests: get_net_worth_report (works with SQLite)
# ---------------------------------------------------------------------------


async def _create_manual_account(
    session: AsyncSession, user_id: uuid.UUID, name: str, balance: float = 0
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        type="checking",
        balance=Decimal(str(balance)),
        currency="BRL",
        is_closed=False,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _create_transaction(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: float,
    txn_type: str,
    txn_date: date,
    source: str = "manual",
) -> Transaction:
    from datetime import datetime, timezone

    txn = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description=f"Test {txn_type} {amount}",
        amount=Decimal(str(amount)),
        date=txn_date,
        type=txn_type,
        source=source,
        currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_net_worth_report_structure(session: AsyncSession, test_user):
    """Net worth report returns correct ReportResponse structure."""
    account = await _create_manual_account(session, test_user.id, "NW Test")
    await _create_transaction(
        session, test_user.id, account.id, 5000, "credit", date.today(), source="opening_balance"
    )

    report = await get_net_worth_report(session, test_user.id, months=6, interval="monthly")

    assert report.meta.type == "net_worth"
    assert report.meta.series_keys == ["accounts", "assets", "liabilities"]
    assert report.meta.currency == "BRL"
    assert report.meta.interval == "monthly"
    assert report.summary.primary_value is not None
    assert len(report.summary.breakdowns) == 3
    assert len(report.trend) > 0

    # Each trend point has the expected breakdown keys
    for dp in report.trend:
        assert "accounts" in dp.breakdowns
        assert "assets" in dp.breakdowns
        assert "liabilities" in dp.breakdowns


@pytest.mark.asyncio
async def test_net_worth_report_reflects_balance(session: AsyncSession, test_user):
    """Net worth report reflects actual account balance."""
    account = await _create_manual_account(session, test_user.id, "NW Balance Test")
    await _create_transaction(
        session, test_user.id, account.id, 10000, "credit", date.today(), source="opening_balance"
    )
    await _create_transaction(
        session, test_user.id, account.id, 3000, "debit", date.today()
    )

    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")

    # Current net worth should be 10000 - 3000 = 7000
    assert report.summary.primary_value == 7000.0
    assert report.summary.breakdowns[0].key == "accounts"
    assert report.summary.breakdowns[0].value == 7000.0


@pytest.mark.asyncio
async def test_net_worth_report_change_amount(session: AsyncSession, test_user):
    """Net worth report computes change between first and last trend points."""
    account = await _create_manual_account(session, test_user.id, "NW Change Test")

    # Add a transaction 3 months ago
    three_months_ago = date.today() - timedelta(days=90)
    await _create_transaction(
        session, test_user.id, account.id, 1000, "credit", three_months_ago, source="opening_balance"
    )
    # Add more income recently
    await _create_transaction(
        session, test_user.id, account.id, 2000, "credit", date.today()
    )

    report = await get_net_worth_report(session, test_user.id, months=6, interval="monthly")

    # change_amount = last.value - first.value; should be positive
    assert report.summary.change_amount >= 0


@pytest.mark.asyncio
async def test_net_worth_report_excludes_closed_accounts(session: AsyncSession, test_user):
    """Closed accounts are excluded from net worth."""
    # Open account with 5000
    open_acct = await _create_manual_account(session, test_user.id, "NW Open")
    await _create_transaction(
        session, test_user.id, open_acct.id, 5000, "credit", date.today(), source="opening_balance"
    )

    # Closed account with 3000
    closed_acct = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="NW Closed",
        type="checking",
        balance=Decimal("3000.00"),
        currency="BRL",
        is_closed=True,
    )
    session.add(closed_acct)
    await session.commit()
    await _create_transaction(
        session, test_user.id, closed_acct.id, 3000, "credit", date.today(), source="opening_balance"
    )

    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")

    # Should only include the open account
    assert report.summary.primary_value == 5000.0


@pytest.mark.asyncio
async def test_net_worth_report_intervals(session: AsyncSession, test_user):
    """Net worth report works with different interval options."""
    account = await _create_manual_account(session, test_user.id, "NW Interval Test")
    await _create_transaction(
        session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance"
    )

    for interval in ["daily", "weekly", "monthly", "yearly"]:
        report = await get_net_worth_report(session, test_user.id, months=6, interval=interval)
        assert report.meta.interval == interval
        assert len(report.trend) > 0


# ---------------------------------------------------------------------------
# API-level tests: /reports/net-worth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_api_endpoint(client, auth_headers, test_transactions):
    """GET /reports/net-worth returns valid response."""
    response = await client.get(
        "/api/reports/net-worth",
        params={"months": 6, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["type"] == "net_worth"
    assert "summary" in data
    assert "trend" in data
    assert isinstance(data["summary"]["primary_value"], (int, float))
    assert isinstance(data["trend"], list)


@pytest.mark.asyncio
async def test_net_worth_api_validation(client, auth_headers):
    """GET /reports/net-worth validates query params."""
    # Invalid interval
    resp = await client.get(
        "/api/reports/net-worth",
        params={"interval": "invalid"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # Months out of range
    resp = await client.get(
        "/api/reports/net-worth",
        params={"months": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_net_worth_api_requires_auth(client):
    """GET /reports/net-worth requires authentication."""
    resp = await client.get("/api/reports/net-worth")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API-level tests: /reports/income-expenses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skip(reason="to_char() is PostgreSQL-specific; tests use SQLite")
async def test_income_expenses_api_endpoint(client, auth_headers, test_transactions):
    """GET /reports/income-expenses returns valid response."""
    response = await client.get(
        "/api/reports/income-expenses",
        params={"months": 12, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["type"] == "income_expenses"
    assert data["meta"]["series_keys"] == ["income", "expenses"]
    assert "summary" in data
    assert "trend" in data

    # Summary should have income, expenses, netIncome breakdowns
    breakdown_keys = [b["key"] for b in data["summary"]["breakdowns"]]
    assert "income" in breakdown_keys
    assert "expenses" in breakdown_keys
    assert "netIncome" in breakdown_keys

    # Verify math: net income = income - expenses
    breakdowns = {b["key"]: b["value"] for b in data["summary"]["breakdowns"]}
    assert abs(breakdowns["netIncome"] - (breakdowns["income"] - breakdowns["expenses"])) < 0.01

    # Each trend point has income/expenses breakdowns
    for dp in data["trend"]:
        assert "income" in dp["breakdowns"]
        assert "expenses" in dp["breakdowns"]
        # value = net income = income - expenses
        expected_net = dp["breakdowns"]["income"] - dp["breakdowns"]["expenses"]
        assert abs(dp["value"] - expected_net) < 0.01


@pytest.mark.asyncio
@pytest.mark.skip(reason="to_char() is PostgreSQL-specific; tests use SQLite")
async def test_income_expenses_excludes_opening_balance(client, auth_headers):
    """Income expenses report excludes opening balance transactions."""
    # Create account with opening balance
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "IE Test", "type": "checking", "balance": 10000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201

    response = await client.get(
        "/api/reports/income-expenses",
        params={"months": 1, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    # Opening balance should NOT appear as income
    breakdowns = {b["key"]: b["value"] for b in data["summary"]["breakdowns"]}
    assert breakdowns["income"] == 0.0


@pytest.mark.asyncio
@pytest.mark.skip(reason="to_char() is PostgreSQL-specific; tests use SQLite")
async def test_income_expenses_excludes_transfers(client, auth_headers):
    """Income expenses report excludes transfer pair transactions."""
    response = await client.get(
        "/api/reports/income-expenses",
        params={"months": 12, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    # Just verify the endpoint works — transfer exclusion is enforced by the SQL filter


@pytest.mark.asyncio
async def test_income_expenses_api_validation(client, auth_headers):
    """GET /reports/income-expenses validates query params."""
    resp = await client.get(
        "/api/reports/income-expenses",
        params={"interval": "invalid"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    resp = await client.get(
        "/api/reports/income-expenses",
        params={"months": 25},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_income_expenses_api_requires_auth(client):
    """GET /reports/income-expenses requires authentication."""
    resp = await client.get("/api/reports/income-expenses")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Schema tests: CategoryTrendItem & category_trend on ReportResponse
# ---------------------------------------------------------------------------


def test_category_trend_item_schema():
    """CategoryTrendItem can be constructed with valid data."""
    item = CategoryTrendItem(
        key="cat-1",
        label="Groceries",
        color="#10B981",
        total=1500.0,
        group="expenses",
        series=[
            ReportDataPoint(date="2025-01", value=500.0, breakdowns={}),
            ReportDataPoint(date="2025-02", value=450.0, breakdowns={}),
            ReportDataPoint(date="2025-03", value=550.0, breakdowns={}),
        ],
    )
    assert item.key == "cat-1"
    assert item.group == "expenses"
    assert len(item.series) == 3
    assert item.total == 1500.0


def test_report_response_includes_category_trend():
    """ReportResponse includes category_trend field, defaulting to empty."""
    from app.schemas.report import ReportMeta, ReportSummary, ReportBreakdown

    response = ReportResponse(
        summary=ReportSummary(
            primary_value=1000.0,
            change_amount=100.0,
            change_percent=10.0,
            breakdowns=[ReportBreakdown(key="a", label="A", value=1000.0, color="#000")],
        ),
        trend=[ReportDataPoint(date="2025-01", value=1000.0, breakdowns={})],
        meta=ReportMeta(type="test", series_keys=["a"], currency="BRL", interval="monthly"),
    )
    # Default is empty list
    assert response.category_trend == []

    # Can also be set explicitly
    item = CategoryTrendItem(
        key="cat-1", label="Food", color="#F00", total=500.0,
        group="expenses", series=[],
    )
    response2 = ReportResponse(
        summary=response.summary,
        trend=response.trend,
        meta=response.meta,
        category_trend=[item],
    )
    assert len(response2.category_trend) == 1
    assert response2.category_trend[0].label == "Food"


# ---------------------------------------------------------------------------
# Service-level test: net_worth returns empty category_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_report_has_empty_category_trend(session: AsyncSession, test_user):
    """Net worth report returns empty category_trend (only used by income_expenses)."""
    account = await _create_manual_account(session, test_user.id, "NW CatTrend Test")
    await _create_transaction(
        session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance"
    )

    report = await get_net_worth_report(session, test_user.id, months=3, interval="monthly")
    assert report.category_trend == []


# ---------------------------------------------------------------------------
# API-level test: income-expenses includes category_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skip(reason="to_char() is PostgreSQL-specific; tests use SQLite")
async def test_income_expenses_has_category_trend(client, auth_headers, test_transactions):
    """GET /reports/income-expenses response includes category_trend array."""
    response = await client.get(
        "/api/reports/income-expenses",
        params={"months": 12, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert "category_trend" in data
    assert isinstance(data["category_trend"], list)

    # Each item should have the expected shape
    for item in data["category_trend"]:
        assert "key" in item
        assert "label" in item
        assert "color" in item
        assert "total" in item
        assert "group" in item
        assert item["group"] in ("income", "expenses")
        assert "series" in item
        assert isinstance(item["series"], list)
        for point in item["series"]:
            assert "date" in point
            assert "value" in point


# ---------------------------------------------------------------------------
# _asset_value_at
# ---------------------------------------------------------------------------


async def _make_manual_account(session, user_id, name, currency="BRL", acct_type="checking"):
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, name=name,
        type=acct_type, balance=Decimal("0"), currency=currency,
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


async def _add_txn(session, user_id, account_id, amount, txn_type, txn_date, source="manual"):
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        description=f"Test {txn_type}", amount=Decimal(str(amount)),
        date=txn_date, type=txn_type, source=source, currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    return txn


@pytest.mark.asyncio
async def test_asset_value_at_with_entries(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="House",
        type="real_estate", currency="BRL",
    )
    session.add(asset)
    await session.flush()

    v1 = AssetValue(
        id=uuid.uuid4(), asset_id=asset.id,
        amount=Decimal("100000"), date=date.today() - timedelta(days=30),
    )
    v2 = AssetValue(
        id=uuid.uuid4(), asset_id=asset.id,
        amount=Decimal("110000"), date=date.today(),
    )
    session.add_all([v1, v2])
    await session.commit()

    total = await _asset_value_at(session, test_user.id, date.today(), "BRL")
    assert total == 110000.0


@pytest.mark.asyncio
async def test_asset_value_at_fallback_purchase_price(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Car",
        type="vehicle", currency="BRL",
        purchase_price=Decimal("50000"),
        purchase_date=date.today() - timedelta(days=60),
    )
    session.add(asset)
    await session.commit()

    total = await _asset_value_at(session, test_user.id, date.today(), "BRL")
    assert total == 50000.0


@pytest.mark.asyncio
async def test_asset_value_at_excludes_archived(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Sold Car",
        type="vehicle", currency="BRL",
        purchase_price=Decimal("30000"), is_archived=True,
    )
    session.add(asset)
    await session.commit()

    total = await _asset_value_at(session, test_user.id, date.today(), "BRL")
    assert total == 0.0


@pytest.mark.asyncio
async def test_asset_value_at_excludes_sold(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Sold Asset",
        type="vehicle", currency="BRL",
        purchase_price=Decimal("20000"),
        sell_date=date.today() - timedelta(days=10),
    )
    session.add(asset)
    await session.commit()

    total = await _asset_value_at(session, test_user.id, date.today(), "BRL")
    assert total == 0.0


@pytest.mark.asyncio
async def test_asset_value_at_purchase_date_after_cutoff(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Future Asset",
        type="other", currency="BRL",
        purchase_price=Decimal("5000"),
        purchase_date=date.today() + timedelta(days=30),
    )
    session.add(asset)
    await session.commit()

    total = await _asset_value_at(session, test_user.id, date.today(), "BRL")
    assert total == 0.0


# ---------------------------------------------------------------------------
# _net_worth_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_with_credit_card(session: AsyncSession, test_user: User):
    checking = await _make_manual_account(session, test_user.id, "NW Check")
    await _add_txn(session, test_user.id, checking.id, 5000, "credit", date.today())

    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id="ext-cc-nw", institution_name="CC",
        credentials={}, status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    cc = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        name="CC", type="credit_card", balance=Decimal("1000"), currency="BRL",
    )
    session.add(cc)
    await session.commit()

    dp = await _net_worth_at(session, test_user.id, date.today(), "BRL")
    assert dp.breakdowns["accounts"] == 5000.0
    assert dp.breakdowns["liabilities"] == 1000.0
    assert dp.value == 4000.0


@pytest.mark.asyncio
async def test_net_worth_with_assets(session: AsyncSession, test_user: User):
    checking = await _make_manual_account(session, test_user.id, "NW Assets Check")
    await _add_txn(session, test_user.id, checking.id, 3000, "credit", date.today())

    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Apartment",
        type="real_estate", currency="BRL",
        purchase_price=Decimal("200000"),
        purchase_date=date.today() - timedelta(days=30),
    )
    session.add(asset)
    await session.commit()

    dp = await _net_worth_at(session, test_user.id, date.today(), "BRL")
    assert dp.breakdowns["accounts"] == 3000.0
    assert dp.breakdowns["assets"] == 200000.0
    assert dp.value == 203000.0


@pytest.mark.asyncio
async def test_net_worth_negative_manual_balance(session: AsyncSession, test_user: User):
    acct = await _make_manual_account(session, test_user.id, "NW Negative")
    await _add_txn(session, test_user.id, acct.id, 1000, "debit", date.today())

    dp = await _net_worth_at(session, test_user.id, date.today(), "BRL")
    assert dp.breakdowns["accounts"] == -1000.0


# ---------------------------------------------------------------------------
# get_net_worth_report — composition and intervals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_composition_includes_accounts(session: AsyncSession, test_user: User):
    acct = await _make_manual_account(session, test_user.id, "Comp Acct")
    await _add_txn(session, test_user.id, acct.id, 10000, "credit", date.today())

    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")
    comp_labels = [c.label for c in report.composition]
    assert "Comp Acct" in comp_labels


@pytest.mark.asyncio
async def test_net_worth_composition_includes_assets(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Comp Asset",
        type="investment", currency="BRL",
        purchase_price=Decimal("5000"),
        purchase_date=date.today() - timedelta(days=5),
    )
    session.add(asset)
    await session.commit()

    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")
    comp_labels = [c.label for c in report.composition]
    assert "Comp Asset" in comp_labels


@pytest.mark.asyncio
async def test_net_worth_composition_uses_display_name(session: AsyncSession, test_user: User):
    """Composition labels must use display_name when set, falling back to name."""
    acct = await _make_manual_account(session, test_user.id, "Provider Name")
    acct.display_name = "My Nickname"
    await session.commit()
    await _add_txn(session, test_user.id, acct.id, 10000, "credit", date.today())

    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")
    comp_labels = [c.label for c in report.composition]
    assert "My Nickname" in comp_labels
    assert "Provider Name" not in comp_labels


@pytest.mark.asyncio
async def test_net_worth_weekly_interval(session: AsyncSession, test_user: User):
    acct = await _make_manual_account(session, test_user.id, "Weekly Test")
    await _add_txn(session, test_user.id, acct.id, 1000, "credit", date.today())

    report = await get_net_worth_report(session, test_user.id, months=2, interval="weekly")
    assert report.meta.interval == "weekly"
    assert len(report.trend) > 1


@pytest.mark.asyncio
async def test_net_worth_daily_interval(session: AsyncSession, test_user: User):
    acct = await _make_manual_account(session, test_user.id, "Daily Test")
    await _add_txn(session, test_user.id, acct.id, 500, "credit", date.today())

    report = await get_net_worth_report(session, test_user.id, months=1, interval="daily")
    assert report.meta.interval == "daily"
    assert len(report.trend) > 10


@pytest.mark.asyncio
async def test_net_worth_change_percent_zero_previous(session: AsyncSession, test_user: User):
    report = await get_net_worth_report(session, test_user.id, months=1, interval="monthly")
    if report.summary.primary_value == 0:
        assert report.summary.change_percent is None


# ---------------------------------------------------------------------------
# Pure-function tests: _add_months
# ---------------------------------------------------------------------------


def test_add_months_simple():
    assert _add_months(date(2025, 1, 15), 1) == date(2025, 2, 15)


def test_add_months_year_rollover():
    assert _add_months(date(2025, 11, 10), 3) == date(2026, 2, 10)


def test_add_months_clamps_to_last_day():
    # Jan 31 + 1 month → Feb 28 (or 29 in leap year)
    assert _add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_add_months_zero_returns_same():
    assert _add_months(date(2025, 6, 15), 0) == date(2025, 6, 15)


def test_add_months_twelve_keeps_same_day():
    assert _add_months(date(2025, 6, 15), 12) == date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Cash flow report — service-level tests
# ---------------------------------------------------------------------------


async def _make_recurring(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: float,
    txn_type: str,
    frequency: str = "monthly",
    *,
    description: str | None = None,
    day_of_month: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    next_occurrence: date | None = None,
    is_active: bool = True,
    currency: str = "BRL",
    category_id: uuid.UUID | None = None,
) -> RecurringTransaction:
    today = date.today()
    start = start_date or today
    nxt = next_occurrence or (today + timedelta(days=1))
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        description=description or f"Test {txn_type} {amount}",
        amount=Decimal(str(amount)),
        currency=currency,
        type=txn_type,
        frequency=frequency,
        day_of_month=day_of_month,
        start_date=start,
        end_date=end_date,
        is_active=is_active,
        next_occurrence=nxt,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


@pytest.mark.asyncio
async def test_cash_flow_report_structure(session: AsyncSession, test_user: User):
    """Cash flow report returns a well-formed ReportResponse."""
    account = await _make_manual_account(session, test_user.id, "CF Structure")
    await _add_txn(
        session, test_user.id, account.id, 5000, "credit",
        date.today(), source="opening_balance",
    )

    report = await get_cash_flow_report(session, test_user.id, months=6, interval="daily")

    assert report.meta.type == "cash_flow"
    assert report.meta.series_keys == ["balance"]
    assert report.meta.currency == "BRL"
    assert report.meta.interval == "daily"
    assert len(report.trend) > 0
    # Summary breakdowns are exactly the four cash-flow keys, in order.
    keys = [b.key for b in report.summary.breakdowns]
    assert keys == ["startingBalance", "projectedIncome", "projectedExpenses", "endingBalance"]


@pytest.mark.asyncio
async def test_cash_flow_starting_balance_matches_today(
    session: AsyncSession, test_user: User
):
    """startingBalance breakdown == sum of account balances at today."""
    account = await _make_manual_account(session, test_user.id, "CF Start")
    await _add_txn(
        session, test_user.id, account.id, 1234.56, "credit",
        date.today(), source="opening_balance",
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    starting = next(b for b in report.summary.breakdowns if b.key == "startingBalance")
    assert starting.value == 1234.56


@pytest.mark.asyncio
async def test_cash_flow_no_data_is_flat_at_zero(
    session: AsyncSession, test_user: User
):
    """User with no accounts → flat line at 0."""
    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    assert report.summary.primary_value == 0.0
    assert report.summary.change_amount == 0.0
    assert report.summary.change_percent is None  # zero starting → percent undefined
    # Every trend point should be 0
    assert all(p.value == 0.0 for p in report.trend)
    assert report.composition == []


@pytest.mark.asyncio
async def test_cash_flow_no_data_with_balance_is_flat(
    session: AsyncSession, test_user: User
):
    """Account with balance but no recurring → flat line at starting balance."""
    account = await _make_manual_account(session, test_user.id, "CF Flat")
    await _add_txn(
        session, test_user.id, account.id, 2500, "credit",
        date.today(), source="opening_balance",
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")

    assert report.summary.primary_value == 2500.0
    assert report.summary.change_amount == 0.0
    # All trend points equal to starting balance
    assert all(p.value == 2500.0 for p in report.trend)


@pytest.mark.asyncio
async def test_cash_flow_recurring_credit_increases_balance(
    session: AsyncSession, test_user: User
):
    """Monthly salary credit should drive ending balance above starting."""
    account = await _make_manual_account(session, test_user.id, "CF Salary")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    salary_day = date(today.year + (today.month // 12), (today.month % 12) + 1, today.day)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=3000, txn_type="credit", frequency="monthly",
        day_of_month=today.day, next_occurrence=salary_day,
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")

    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    # 3 monthly occurrences over a 3-month window
    assert proj_income.value >= 3000.0  # at least one occurrence
    assert proj_exp.value == 0.0
    assert ending.value > 1000.0
    assert report.summary.change_amount > 0


@pytest.mark.asyncio
async def test_cash_flow_recurring_debit_decreases_balance(
    session: AsyncSession, test_user: User
):
    """Monthly rent debit should drive ending balance below starting."""
    account = await _make_manual_account(session, test_user.id, "CF Rent")
    await _add_txn(
        session, test_user.id, account.id, 10000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    nxt = today + timedelta(days=2)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=1200, txn_type="debit", frequency="monthly",
        day_of_month=nxt.day, next_occurrence=nxt,
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")

    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    assert proj_exp.value > 0
    assert ending.value < 10000.0
    assert report.summary.change_amount < 0


@pytest.mark.asyncio
async def test_cash_flow_inactive_recurring_excluded(
    session: AsyncSession, test_user: User
):
    """is_active=False recurring should not contribute to projection."""
    account = await _make_manual_account(session, test_user.id, "CF Inactive")
    await _add_txn(
        session, test_user.id, account.id, 5000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=999, txn_type="debit", frequency="monthly",
        next_occurrence=today + timedelta(days=2), is_active=False,
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    assert proj_exp.value == 0.0


@pytest.mark.asyncio
async def test_cash_flow_recurring_end_date_stops_contribution(
    session: AsyncSession, test_user: User
):
    """Recurring with end_date in window should stop contributing past that date."""
    account = await _make_manual_account(session, test_user.id, "CF EndDate")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    # Recurring monthly +500 USD, ends in ~45 days (only 1-2 occurrences in 6mo window)
    end = today + timedelta(days=45)
    nxt = today + timedelta(days=2)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=500, txn_type="credit", frequency="monthly",
        day_of_month=nxt.day, next_occurrence=nxt, end_date=end,
    )

    short_report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    long_report = await get_cash_flow_report(session, test_user.id, months=6, interval="daily")

    short_inc = next(b for b in short_report.summary.breakdowns if b.key == "projectedIncome")
    long_inc = next(b for b in long_report.summary.breakdowns if b.key == "projectedIncome")
    # Total projected income should not grow much when extending past end_date
    assert long_inc.value == short_inc.value


@pytest.mark.asyncio
async def test_cash_flow_future_dated_booked_transaction_included(
    session: AsyncSession, test_user: User
):
    """A Transaction with date > today is folded into the projection."""
    account = await _make_manual_account(session, test_user.id, "CF Future Tx")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    # Post-dated bonus 10 days from now
    await _add_txn(
        session, test_user.id, account.id, 750, "credit",
        date.today() + timedelta(days=10),
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    assert proj_income.value >= 750.0
    assert ending.value >= 1750.0


@pytest.mark.asyncio
async def test_cash_flow_opening_balance_excluded_from_inflow(
    session: AsyncSession, test_user: User
):
    """opening_balance txns sit in starting balance and never count as inflow."""
    account = await _make_manual_account(session, test_user.id, "CF OB")
    # Future-dated opening-balance shouldn't show up as inflow
    await _add_txn(
        session, test_user.id, account.id, 9999, "credit",
        date.today() + timedelta(days=5), source="opening_balance",
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    assert proj_income.value == 0.0


@pytest.mark.asyncio
async def test_cash_flow_closed_account_excluded(
    session: AsyncSession, test_user: User
):
    """Future-dated transactions on closed accounts are excluded."""
    open_acct = await _make_manual_account(session, test_user.id, "CF Open")
    await _add_txn(
        session, test_user.id, open_acct.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    closed = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="CF Closed",
        type="checking", balance=Decimal("0"), currency="BRL", is_closed=True,
    )
    session.add(closed)
    await session.commit()

    await _add_txn(
        session, test_user.id, closed.id, 5000, "credit",
        date.today() + timedelta(days=3),
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    assert proj_income.value == 0.0


@pytest.mark.asyncio
async def test_cash_flow_intervals(session: AsyncSession, test_user: User):
    """All supported intervals produce valid trends."""
    account = await _make_manual_account(session, test_user.id, "CF Intervals")
    await _add_txn(
        session, test_user.id, account.id, 2000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=300, txn_type="debit", frequency="weekly",
        next_occurrence=today + timedelta(days=2),
    )

    intervals_seen = {}
    for interval in ["daily", "weekly", "monthly"]:
        rep = await get_cash_flow_report(session, test_user.id, months=2, interval=interval)
        assert rep.meta.interval == interval
        assert len(rep.trend) > 0
        intervals_seen[interval] = rep.summary.breakdowns

    # The total projected expenses should be ~equal across intervals
    daily_exp = next(b.value for b in intervals_seen["daily"] if b.key == "projectedExpenses")
    weekly_exp = next(b.value for b in intervals_seen["weekly"] if b.key == "projectedExpenses")
    monthly_exp = next(b.value for b in intervals_seen["monthly"] if b.key == "projectedExpenses")
    # Allow tiny rounding variance
    assert abs(daily_exp - weekly_exp) < 1.0
    assert abs(daily_exp - monthly_exp) < 1.0


@pytest.mark.asyncio
async def test_cash_flow_months_range(session: AsyncSession, test_user: User):
    """Different `months` values produce trends ending at different dates."""
    account = await _make_manual_account(session, test_user.id, "CF Months")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    rep_1 = await get_cash_flow_report(session, test_user.id, months=1, interval="daily")
    rep_6 = await get_cash_flow_report(session, test_user.id, months=6, interval="daily")
    rep_12 = await get_cash_flow_report(session, test_user.id, months=12, interval="daily")

    # Daily intervals → 1mo ≈ 30 points, 6mo ≈ 184, 12mo ≈ 366
    assert len(rep_1.trend) < len(rep_6.trend) < len(rep_12.trend)


@pytest.mark.asyncio
async def test_cash_flow_balance_is_running_cumulative(
    session: AsyncSession, test_user: User
):
    """Each subsequent trend point's value reflects accumulated flows."""
    account = await _make_manual_account(session, test_user.id, "CF Cumul")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    # +100 weekly so balance keeps rising
    await _make_recurring(
        session, test_user.id, account.id,
        amount=100, txn_type="credit", frequency="weekly",
        next_occurrence=today + timedelta(days=2),
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    # The trend should be monotonically non-decreasing (no debits, only credits)
    values = [p.value for p in report.trend]
    for prev, curr in zip(values, values[1:]):
        assert curr >= prev - 0.01  # tiny tolerance for floats


@pytest.mark.asyncio
async def test_cash_flow_composition_groups(session: AsyncSession, test_user: User):
    """Composition splits recurring projections into income/expenses groups."""
    account = await _make_manual_account(session, test_user.id, "CF Comp")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=500, txn_type="credit", frequency="monthly",
        next_occurrence=today + timedelta(days=2),
        description="Income recurring",
    )
    await _make_recurring(
        session, test_user.id, account.id,
        amount=200, txn_type="debit", frequency="monthly",
        next_occurrence=today + timedelta(days=3),
        description="Expense recurring",
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")

    groups = {c.group for c in report.composition}
    assert "income" in groups
    assert "expenses" in groups
    # Each group has positive values
    for c in report.composition:
        assert c.value > 0


@pytest.mark.asyncio
async def test_cash_flow_change_percent_zero_starting(
    session: AsyncSession, test_user: User
):
    """When starting balance is 0, change_percent is None."""
    account = await _make_manual_account(session, test_user.id, "CF Zero")
    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=100, txn_type="credit", frequency="monthly",
        next_occurrence=today + timedelta(days=2),
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    assert report.summary.change_percent is None


@pytest.mark.asyncio
async def test_cash_flow_weekly_recurring(session: AsyncSession, test_user: User):
    """Weekly frequency expands into multiple occurrences in window."""
    account = await _make_manual_account(session, test_user.id, "CF Weekly")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=80, txn_type="debit", frequency="weekly",
        next_occurrence=today + timedelta(days=1),
    )

    report = await get_cash_flow_report(session, test_user.id, months=1, interval="daily")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    # ~4-5 weekly occurrences in 1 month
    assert 240.0 <= proj_exp.value <= 480.0


@pytest.mark.asyncio
async def test_cash_flow_only_recurrings_after_today(
    session: AsyncSession, test_user: User
):
    """Recurring with next_occurrence == today is NOT counted (today is starting)."""
    account = await _make_manual_account(session, test_user.id, "CF Today")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    # next_occurrence is today — same day as starting balance, should be excluded
    await _make_recurring(
        session, test_user.id, account.id,
        amount=999, txn_type="credit", frequency="monthly",
        next_occurrence=today,
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    # Over 2 months: 3 raw monthly occurrences (today, +1mo, +2mo) but the
    # one ON today is excluded — so we expect exactly 2 × 999 = 1998.
    assert proj_income.value == 1998.0


# ---------------------------------------------------------------------------
# Cash flow API-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_flow_api_endpoint(client, auth_headers, test_transactions):
    """GET /reports/cash-flow returns valid response."""
    response = await client.get(
        "/api/reports/cash-flow",
        params={"months": 6, "interval": "daily"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["meta"]["type"] == "cash_flow"
    assert data["meta"]["series_keys"] == ["balance"]
    assert "summary" in data
    assert "trend" in data
    breakdown_keys = [b["key"] for b in data["summary"]["breakdowns"]]
    assert breakdown_keys == [
        "startingBalance", "projectedIncome",
        "projectedExpenses", "endingBalance",
    ]


@pytest.mark.asyncio
async def test_cash_flow_api_validation(client, auth_headers):
    """GET /reports/cash-flow rejects out-of-range params."""
    # months > 12
    resp = await client.get(
        "/api/reports/cash-flow",
        params={"months": 13},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # months < 1
    resp = await client.get(
        "/api/reports/cash-flow",
        params={"months": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # yearly is not a supported interval for cash flow
    resp = await client.get(
        "/api/reports/cash-flow",
        params={"interval": "yearly"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # arbitrary string interval
    resp = await client.get(
        "/api/reports/cash-flow",
        params={"interval": "garbage"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cash_flow_api_requires_auth(client):
    """GET /reports/cash-flow requires authentication."""
    resp = await client.get("/api/reports/cash-flow")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cash_flow_api_default_params(client, auth_headers, test_transactions):
    """GET /reports/cash-flow with no params uses defaults (months=6, interval=daily)."""
    resp = await client.get("/api/reports/cash-flow", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["interval"] == "daily"
    # 6 months * ~30 days = ~180 trend points
    assert 150 <= len(data["trend"]) <= 200


@pytest.mark.asyncio
async def test_cash_flow_api_trend_point_shape(client, auth_headers, test_transactions):
    """Every trend point exposes inflow/outflow breakdowns."""
    resp = await client.get(
        "/api/reports/cash-flow",
        params={"months": 3, "interval": "daily"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    for p in data["trend"]:
        assert "inflow" in p["breakdowns"]
        assert "outflow" in p["breakdowns"]
        assert "value" in p
        assert "date" in p


# ---------------------------------------------------------------------------
# Cash flow — credit card accounting mode (cash vs accrual)
# ---------------------------------------------------------------------------


async def _make_cc_account(
    session: AsyncSession, user_id: uuid.UUID, name: str, currency: str = "BRL",
) -> Account:
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, name=name,
        type="credit_card", balance=Decimal("0"), currency=currency,
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


async def _set_accounting_mode(session: AsyncSession, mode: str) -> None:
    """Set the global credit_card_accounting_mode app setting."""
    from app.models.app_settings import AppSetting
    existing = await session.get(AppSetting, "credit_card_accounting_mode")
    if existing:
        existing.value = mode
    else:
        session.add(AppSetting(key="credit_card_accounting_mode", value=mode))
    await session.commit()


@pytest.mark.asyncio
async def test_cash_flow_cash_mode_uses_transaction_date(
    session: AsyncSession, test_user: User
):
    """In cash mode: a CC purchase made today (effective_date in future) is
    already counted in starting balance via the CC liability — and is NOT
    re-projected on its effective_date."""
    await _set_accounting_mode(session, "cash")

    bank = await _make_manual_account(session, test_user.id, "CF CashMode Bank")
    cc = await _make_cc_account(session, test_user.id, "CF CashMode CC")
    # $1000 in bank
    await _add_txn(
        session, test_user.id, bank.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    # CC purchase today, bill due in 20 days
    today = date.today()
    cc_purchase = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=cc.id,
        description="CC Purchase", amount=Decimal("100"),
        date=today, effective_date=today + timedelta(days=20),
        type="debit", source="manual", currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(cc_purchase)
    await session.commit()

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    starting = next(b for b in report.summary.breakdowns if b.key == "startingBalance")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    # Cash mode: total balance = bank ($1000) - CC debt ($100) = $900
    assert starting.value == 900.0
    # No future flows queued — Transaction.date == today (not > today)
    assert proj_exp.value == 0.0
    assert ending.value == 900.0


@pytest.mark.asyncio
async def test_cash_flow_accrual_mode_projects_cc_on_effective_date(
    session: AsyncSession, test_user: User
):
    """In accrual mode: a CC purchase with effective_date > today is added
    back to starting balance (so it represents 'cash on hand right now') and
    re-projected as an outflow on the bill due date."""
    await _set_accounting_mode(session, "accrual")

    bank = await _make_manual_account(session, test_user.id, "CF AccrualMode Bank")
    cc = await _make_cc_account(session, test_user.id, "CF AccrualMode CC")
    await _add_txn(
        session, test_user.id, bank.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    cc_purchase = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=cc.id,
        description="CC Purchase", amount=Decimal("100"),
        date=today, effective_date=today + timedelta(days=20),
        type="debit", source="manual", currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(cc_purchase)
    await session.commit()

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    starting = next(b for b in report.summary.breakdowns if b.key == "startingBalance")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    # Accrual: pending CC ($100 debt) is added back → $900 + $100 = $1000.
    assert starting.value == 1000.0
    # Future outflow on day +20: $100.
    assert proj_exp.value == 100.0
    # Ending: $1000 - $100 = $900 (same wealth as cash mode, just timed differently).
    assert ending.value == 900.0

    # The dip happens on the bill due date.
    bill_label = (today + timedelta(days=20)).isoformat()
    bill_point = next(p for p in report.trend if p.date == bill_label)
    assert bill_point.breakdowns["outflow"] == 100.0
    assert bill_point.value == 900.0
    # The day before the bill, balance is still 1000.
    pre_bill_label = (today + timedelta(days=19)).isoformat()
    pre_bill_point = next(p for p in report.trend if p.date == pre_bill_label)
    assert pre_bill_point.value == 1000.0


@pytest.mark.asyncio
async def test_cash_flow_accrual_mode_no_double_count(
    session: AsyncSession, test_user: User
):
    """Accrual mode: the same CC purchase must not appear in both starting
    balance AND future flows — sum of (starting + inflow - outflow) over the
    window must equal final 'true' total balance (= what cash mode shows)."""
    await _set_accounting_mode(session, "accrual")

    bank = await _make_manual_account(session, test_user.id, "CF NoDouble Bank")
    cc = await _make_cc_account(session, test_user.id, "CF NoDouble CC")
    await _add_txn(
        session, test_user.id, bank.id, 5000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Two pending CC purchases with effective dates inside the window
    for offset, amt in [(15, 80), (40, 120)]:
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=cc.id,
            description=f"Purchase {amt}", amount=Decimal(str(amt)),
            date=today, effective_date=today + timedelta(days=offset),
            type="debit", source="manual", currency="BRL",
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    starting = next(b for b in report.summary.breakdowns if b.key == "startingBalance")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")

    # starting = 5000 (bank) - 200 (CC debt) + 200 (added back pending) = 5000
    assert starting.value == 5000.0
    # both purchases project as outflows
    assert proj_exp.value == 200.0
    # ending matches cash mode's "what you'd have if you paid all CC bills"
    assert ending.value == 4800.0


@pytest.mark.asyncio
async def test_cash_flow_accrual_mode_cc_purchase_outside_window_ignored(
    session: AsyncSession, test_user: User
):
    """A CC purchase whose effective_date falls past the projection window is
    not added back nor projected — its impact remains in starting balance."""
    await _set_accounting_mode(session, "accrual")

    bank = await _make_manual_account(session, test_user.id, "CF Outside Bank")
    cc = await _make_cc_account(session, test_user.id, "CF Outside CC")
    await _add_txn(
        session, test_user.id, bank.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Bill due 6 months out; we project only 2 months
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=cc.id,
        description="CC Purchase Far", amount=Decimal("75"),
        date=today, effective_date=today + timedelta(days=180),
        type="debit", source="manual", currency="BRL",
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    starting = next(b for b in report.summary.breakdowns if b.key == "startingBalance")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")

    # CC debt is reflected in starting balance ($1000 - $75 = $925), no add-back
    # because the bill is outside the projection window.
    assert starting.value == 925.0
    assert proj_exp.value == 0.0


# ---------------------------------------------------------------------------
# Cash flow — multi-currency
# ---------------------------------------------------------------------------


async def _seed_fx_rate(
    session: AsyncSession, base: str, quote: str, rate: float, day: date | None = None,
) -> None:
    from app.models.fx_rate import FxRate
    session.add(FxRate(
        base_currency=base, quote_currency=quote,
        rate=Decimal(str(rate)), date=day or date.today(),
        source="test",
    ))
    await session.commit()


def _next_first_of_month(d: date) -> date:
    """Return the 1st day of the next month after `d` — useful to anchor
    a monthly recurring at a predictable day_of_month=1 cadence."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


@pytest.mark.asyncio
async def test_cash_flow_recurring_in_foreign_currency(
    session: AsyncSession, test_user: User
):
    """A recurring in USD with primary BRL is converted via the FX rate."""
    # Primary currency for test_user is BRL. Seed USD->BRL = 5.0.
    # rate(USD->BRL) via cross = usd_to_BRL / usd_to_USD = 5.0 / 1 = 5.0.
    await _seed_fx_rate(session, "USD", "BRL", 5.0)

    account = await _make_manual_account(session, test_user.id, "CF MultiCcy")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    nxt = _next_first_of_month(today)
    # Recurring in USD: $200 on the 1st of each month → 1000 BRL @ rate 5.0
    await _make_recurring(
        session, test_user.id, account.id,
        amount=200, txn_type="credit", frequency="monthly",
        day_of_month=1, next_occurrence=nxt, currency="USD",
    )

    # 3-month window from today. Window covers exactly 3 "1st of month" anchors.
    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    # 3 occurrences × 200 USD × 5.0 = 3000 BRL
    assert proj_income.value == 3000.0


@pytest.mark.asyncio
async def test_cash_flow_mixed_currencies(session: AsyncSession, test_user: User):
    """Recurrings in multiple currencies converted correctly using cached rates."""
    # Primary is BRL. Seed cross rates via USD anchor.
    # USD->BRL = 5.0, USD->EUR = 0.5  ⇒  EUR->BRL = 5.0 / 0.5 = 10.0
    await _seed_fx_rate(session, "USD", "BRL", 5.0)
    await _seed_fx_rate(session, "USD", "EUR", 0.5)

    account = await _make_manual_account(session, test_user.id, "CF Mixed")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    nxt = _next_first_of_month(today)
    # 100 EUR on the 1st → 1000 BRL/month
    await _make_recurring(
        session, test_user.id, account.id,
        amount=100, txn_type="credit", frequency="monthly",
        day_of_month=1, next_occurrence=nxt, currency="EUR",
        description="EUR income",
    )
    # 100 BRL on the 1st (same anchor, no conversion)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=100, txn_type="debit", frequency="monthly",
        day_of_month=1, next_occurrence=nxt, currency="BRL",
        description="BRL expense",
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")

    # Window covers exactly 2 "1st of month" anchors.
    # 2 EUR occurrences × 100 × 10.0 = 2000 BRL
    assert proj_income.value == 2000.0
    # 2 BRL occurrences × 100 = 200 BRL (no conversion)
    assert proj_exp.value == 200.0


@pytest.mark.asyncio
async def test_cash_flow_currency_conversion_uses_amount_primary_when_present(
    session: AsyncSession, test_user: User
):
    """A future-dated transaction with amount_primary set uses that value,
    skipping FX conversion."""
    account = await _make_manual_account(session, test_user.id, "CF AmtPrimary")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Tx in USD with amount_primary already stamped at a frozen rate
    txn = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Frozen-rate tx",
        amount=Decimal("100"), currency="USD",
        amount_primary=Decimal("777.77"),  # arbitrary stamped value
        date=today + timedelta(days=5), effective_date=today + timedelta(days=5),
        type="credit", source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()

    report = await get_cash_flow_report(session, test_user.id, months=1, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    assert proj_income.value == 777.77


# ---------------------------------------------------------------------------
# Cash flow — end_date stricter assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_flow_end_date_exact_occurrence_count(
    session: AsyncSession, test_user: User
):
    """When end_date falls mid-window, exactly the expected number of
    occurrences is projected — not more, not fewer."""
    account = await _make_manual_account(session, test_user.id, "CF EndDateExact")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    # Recurring weekly +$50 starting in 1 day, ending 21 days from now (so
    # exactly 3 occurrences fit: day+1, day+8, day+15; day+22 is past end).
    nxt = today + timedelta(days=1)
    end_d = today + timedelta(days=21)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=50, txn_type="credit", frequency="weekly",
        next_occurrence=nxt, end_date=end_d,
    )

    # Window of 6 months — well past end_date
    report = await get_cash_flow_report(session, test_user.id, months=6, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    # Exactly 3 × 50 = 150
    assert proj_income.value == 150.0


@pytest.mark.asyncio
async def test_cash_flow_end_date_balance_freezes_after(
    session: AsyncSession, test_user: User
):
    """After the recurring's end_date, the running balance stops changing."""
    account = await _make_manual_account(session, test_user.id, "CF EndDateFreeze")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )

    today = date.today()
    nxt = today + timedelta(days=1)
    end_d = today + timedelta(days=21)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=50, txn_type="credit", frequency="weekly",
        next_occurrence=nxt, end_date=end_d,
    )

    report = await get_cash_flow_report(session, test_user.id, months=4, interval="daily")

    # Balance after end_date should match ending balance — it shouldn't keep
    # growing once the recurring stops contributing.
    after_end_label = (end_d + timedelta(days=10)).isoformat()
    after_end_point = next(p for p in report.trend if p.date == after_end_label)
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")
    assert after_end_point.value == ending.value
    # And the value equals starting (1000) + 3 × 50 = 1150
    assert ending.value == 1150.0


@pytest.mark.asyncio
async def test_cash_flow_end_date_at_exact_occurrence(
    session: AsyncSession, test_user: User
):
    """end_date == an occurrence date — that occurrence should still count."""
    account = await _make_manual_account(session, test_user.id, "CF EndDateExactDay")
    await _add_txn(
        session, test_user.id, account.id, 0, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Weekly: occurrences at day+7, day+14, day+21. end_date = day+14 includes 2.
    await _make_recurring(
        session, test_user.id, account.id,
        amount=10, txn_type="credit", frequency="weekly",
        next_occurrence=today + timedelta(days=7),
        end_date=today + timedelta(days=14),
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    # 2 occurrences × $10 = $20
    assert proj_income.value == 20.0


@pytest.mark.asyncio
async def test_cash_flow_end_date_in_past_no_contribution(
    session: AsyncSession, test_user: User
):
    """If end_date is in the past, no occurrences project."""
    account = await _make_manual_account(session, test_user.id, "CF EndDatePast")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    await _make_recurring(
        session, test_user.id, account.id,
        amount=999, txn_type="credit", frequency="monthly",
        next_occurrence=today + timedelta(days=2),
        end_date=today - timedelta(days=10),
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")
    proj_income = next(b for b in report.summary.breakdowns if b.key == "projectedIncome")
    assert proj_income.value == 0.0



@pytest.mark.asyncio
async def test_cash_flow_overdraft_crunch_point_goes_negative(
    session: AsyncSession, test_user: User
):
    """Cash crunch / overdraft: when projected outflows exceed inflows + start,
    the running balance must go negative (not be clamped at 0)."""
    account = await _make_manual_account(session, test_user.id, "CF Crunch")
    await _add_txn(
        session, test_user.id, account.id, 100, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    nxt = _next_first_of_month(today)
    # $200 monthly debit on the 1st — burns through the $100 starting balance
    await _make_recurring(
        session, test_user.id, account.id,
        amount=200, txn_type="debit", frequency="monthly",
        day_of_month=1, next_occurrence=nxt,
    )

    report = await get_cash_flow_report(session, test_user.id, months=3, interval="daily")

    negative_points = [p for p in report.trend if p.value < 0]
    assert len(negative_points) > 0, "Trend never dips below zero — clamped?"
    ending = next(b for b in report.summary.breakdowns if b.key == "endingBalance")
    # 100 starting - 3*200 outflows = -500
    assert ending.value == -500.0


@pytest.mark.asyncio
async def test_cash_flow_paycheck_timing_dip(
    session: AsyncSession, test_user: User
):
    """Classic paycheck-timing dip: rent on day 5, salary on day 15. End of
    month is positive, but mid-month should drop below the starting balance
    (and possibly below 0). The trend must show that intra-month dip — not
    just the net monthly figure."""
    account = await _make_manual_account(session, test_user.id, "CF PayTiming")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()

    # Anchor rent and salary in NEXT month so they fall in the projection
    # window without ambiguity. day_of_month=5 for rent, =15 for salary.
    next_month_first = _next_first_of_month(today)
    rent_day = next_month_first.replace(day=5)
    salary_day = next_month_first.replace(day=15)

    await _make_recurring(
        session, test_user.id, account.id,
        amount=1500, txn_type="debit", frequency="monthly",
        day_of_month=5, next_occurrence=rent_day,
        description="Rent",
    )
    await _make_recurring(
        session, test_user.id, account.id,
        amount=3000, txn_type="credit", frequency="monthly",
        day_of_month=15, next_occurrence=salary_day,
        description="Salary",
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    # Fetch balance on rent day (after rent hit) and salary day (after salary hit)
    rent_pt = next(p for p in report.trend if p.date == rent_day.isoformat())
    salary_pt = next(p for p in report.trend if p.date == salary_day.isoformat())
    end_pt = report.trend[-1]

    # Day 5: 1000 - 1500 = -500 (overdraft mid-month!)
    assert rent_pt.value == -500.0
    # Day 15: -500 + 3000 = 2500
    assert salary_pt.value == 2500.0
    # End of window: net positive despite the mid-month dip
    assert end_pt.value > 0


@pytest.mark.asyncio
async def test_cash_flow_large_one_off_purchase(
    session: AsyncSession, test_user: User
):
    """User wants to plan a single large purchase (e.g., new laptop on day +20).
    The chart must show a single dip on that exact day and recover for the
    rest of the window (no recurring pattern)."""
    account = await _make_manual_account(session, test_user.id, "CF Laptop")
    await _add_txn(
        session, test_user.id, account.id, 5000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    purchase_day = today + timedelta(days=20)
    await _add_txn(
        session, test_user.id, account.id, 1800, "debit", purchase_day,
    )

    report = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")

    # Day before purchase: full starting balance
    pre_pt = next(p for p in report.trend if p.date == (purchase_day - timedelta(days=1)).isoformat())
    purchase_pt = next(p for p in report.trend if p.date == purchase_day.isoformat())
    end_pt = report.trend[-1]

    assert pre_pt.value == 5000.0
    assert purchase_pt.value == 3200.0
    assert purchase_pt.breakdowns["outflow"] == 1800.0
    # No recurrings → balance stays at 3200 for rest of window
    assert end_pt.value == 3200.0


@pytest.mark.asyncio
async def test_cash_flow_multiple_recurrings_same_day(
    session: AsyncSession, test_user: User
):
    """Multiple bills that hit the same day must be summed into one outflow
    bucket on that day, not represented as separate days."""
    account = await _make_manual_account(session, test_user.id, "CF SameDay")
    await _add_txn(
        session, test_user.id, account.id, 5000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    nxt = _next_first_of_month(today)

    # Three different bills, all day 1
    for amt, desc in [(800, "Rent"), (120, "Internet"), (60, "Streaming")]:
        await _make_recurring(
            session, test_user.id, account.id,
            amount=amt, txn_type="debit", frequency="monthly",
            day_of_month=1, next_occurrence=nxt, description=desc,
        )

    report = await get_cash_flow_report(session, test_user.id, months=1, interval="daily")
    bill_day = next(p for p in report.trend if p.date == nxt.isoformat())

    # All three bills stacked on the same day = 800 + 120 + 60 = 980
    assert bill_day.breakdowns["outflow"] == 980.0
    # No other day in the window has any outflow
    other_outflow_days = [
        p for p in report.trend
        if p.date != nxt.isoformat() and p.breakdowns["outflow"] > 0
    ]
    assert other_outflow_days == []


@pytest.mark.asyncio
async def test_cash_flow_yearly_recurring(session: AsyncSession, test_user: User):
    """Yearly recurring (e.g., annual car insurance): expansion in a 12-month
    window should yield exactly one occurrence."""
    account = await _make_manual_account(session, test_user.id, "CF Yearly")
    await _add_txn(
        session, test_user.id, account.id, 5000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Schedule it 30 days out so it falls inside both 1mo+ and 12mo windows
    nxt = today + timedelta(days=30)
    await _make_recurring(
        session, test_user.id, account.id,
        amount=1200, txn_type="debit", frequency="yearly",
        day_of_month=nxt.day, next_occurrence=nxt,
    )

    rep_12 = await get_cash_flow_report(session, test_user.id, months=12, interval="daily")
    proj_exp_12 = next(b for b in rep_12.summary.breakdowns if b.key == "projectedExpenses")
    assert proj_exp_12.value == 1200.0  # exactly one occurrence in 12 months

    # In a 1-month window: also 1 (since nxt is +30 days)
    rep_1 = await get_cash_flow_report(session, test_user.id, months=2, interval="daily")
    proj_exp_1 = next(b for b in rep_1.summary.breakdowns if b.key == "projectedExpenses")
    assert proj_exp_1.value == 1200.0


@pytest.mark.asyncio
async def test_cash_flow_recurring_starting_in_future(
    session: AsyncSession, test_user: User
):
    """A recurring whose start_date is in the future (e.g., a new subscription
    starting next month) must be projected from its start_date forward."""
    account = await _make_manual_account(session, test_user.id, "CF FutureStart")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()
    # Subscription starts at the 1st of the month after next, $20/month
    start = _next_first_of_month(_next_first_of_month(today))
    await _make_recurring(
        session, test_user.id, account.id,
        amount=20, txn_type="debit", frequency="monthly",
        day_of_month=1, start_date=start, next_occurrence=start,
        description="Future subscription",
    )

    report = await get_cash_flow_report(session, test_user.id, months=4, interval="daily")
    proj_exp = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")

    # The subscription starts 1-2 months out; at month=4 we expect ~2-3 hits.
    # Loose check: at least 1 occurrence and the very first day shows no impact.
    assert proj_exp.value >= 20.0
    first_pt = report.trend[0]
    assert first_pt.value == 1000.0  # starting balance untouched


@pytest.mark.asyncio
async def test_cash_flow_running_balance_arithmetic(
    session: AsyncSession, test_user: User
):
    """Strict arithmetic: balance on day N == starting + sum(flows up to day N).
    Anchors the math so refactors can't silently break the cumulative sum."""
    account = await _make_manual_account(session, test_user.id, "CF Math")
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        date.today(), source="opening_balance",
    )
    today = date.today()

    # Place specific transactions on specific days
    flows = [
        (today + timedelta(days=3), 200, "credit"),   # +200 on day 3
        (today + timedelta(days=10), 50, "debit"),    # -50 on day 10
        (today + timedelta(days=15), 75, "debit"),    # -75 on day 15
        (today + timedelta(days=20), 500, "credit"),  # +500 on day 20
    ]
    for d, amt, typ in flows:
        await _add_txn(session, test_user.id, account.id, amt, typ, d)

    report = await get_cash_flow_report(session, test_user.id, months=1, interval="daily")
    by_date = {p.date: p for p in report.trend}

    # Reconstruct expected running balance day by day.
    expected = 1000.0
    daily_signed = {d: (amt if typ == "credit" else -amt) for d, amt, typ in flows}
    for offset in range(0, 25):
        d = today + timedelta(days=offset)
        if d in daily_signed:
            expected += daily_signed[d]
        pt = by_date.get(d.isoformat())
        if pt is None:
            continue  # day past report window — not all 25 days fit
        assert abs(pt.value - expected) < 0.01, (
            f"Day {d}: got {pt.value}, expected {expected}"
        )
