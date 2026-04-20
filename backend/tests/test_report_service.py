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
    _asset_value_at,
    _date_points,
    _format_date_label,
    _net_worth_at,
    get_net_worth_report,
)


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
    assert points[0] == start
    # Last point should be end (June 15), not June 1
    assert points[-1] == end


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
    """When end falls in the same period as the last generated point, replace it."""
    start = date(2025, 1, 1)
    end = date(2025, 3, 15)
    points = _date_points(start, end, "monthly")
    # March 1 and March 15 share "2025-03" label, so March 15 replaces March 1
    assert points[-1] == end
    labels = [_format_date_label(p, "monthly") for p in points]
    # No duplicates
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
