"""API tests for transaction export, rules, dashboard balance history, and import preview."""
import csv
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.account import Account
from app.models.transaction import Transaction


# ---------------------------------------------------------------------------
# Transactions API — export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_transactions_csv(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """GET /api/transactions/export returns CSV with proper headers."""
    response = await client.get("/api/transactions/export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    content = response.text
    # Should have header row + transaction rows
    lines = content.strip().split("\n")
    assert len(lines) > 1
    header = lines[0].replace("\ufeff", "")  # Remove BOM
    columns = next(csv.reader([header]))
    assert "date" in columns
    assert "description" in columns
    assert "amount" in columns
    assert "reporting_date" in columns
    assert "reporting_date_override" in columns


@pytest.mark.asyncio
async def test_export_transactions_with_search(
    client: AsyncClient, auth_headers, test_transactions,
):
    """Export with a search query keeps only the matching transactions."""
    response = await client.get(
        "/api/transactions/export",
        params={"q": "UBER"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(response.text.lstrip("\ufeff").splitlines()))
    assert [row["description"] for row in rows] == ["UBER TRIP"]


def test_csv_safe_neutralises_formula_leaders():
    """Free text keeps its own characters but never becomes a live formula."""
    from app.api.transactions import _csv_safe

    for payload in ("=1+1", "+SUM(A1)", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"):
        assert _csv_safe(payload) == "'" + payload
    assert _csv_safe("Coffee") == "Coffee"
    assert _csv_safe("") == ""
    assert _csv_safe(None) == ""


@pytest.mark.asyncio
async def test_export_neutralises_a_formula_description(
    client: AsyncClient, auth_headers, test_account,
):
    """A description a spreadsheet would run is exported as inert text (#1448)."""
    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "=HYPERLINK(\"evil.example\",\"click\")",
            "notes": "@SUM(A1:A9)",
            "amount": "32.50",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert created.status_code == 201, created.text

    response = await client.get("/api/transactions/export", headers=auth_headers)
    assert response.status_code == 200

    rows = list(csv.DictReader(response.text.lstrip("﻿").splitlines()))
    (row,) = [r for r in rows if "evil" in r["description"]]
    # The characters are all still there; only the leading quote makes the cell
    # text instead of a formula.
    assert row["description"].startswith("'=HYPERLINK")
    assert row["notes"] == "'@SUM(A1:A9)"
    # The amount column stays a number a spreadsheet can add up.
    assert Decimal(row["amount"]) == Decimal("32.50")


# ---------------------------------------------------------------------------
# Rules API — missing coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_rule(
    client: AsyncClient, auth_headers, test_rules, test_categories,
):
    """Creating a rule with duplicate name returns 409."""
    existing_name = test_rules[0].name
    response = await client.post(
        "/api/rules",
        headers=auth_headers,
        json={
            "name": existing_name,
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": "TEST"}],
            "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
            "priority": 5,
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_install_rule_pack_not_found(client: AsyncClient, auth_headers, test_categories):
    """POST /api/rules/packs/nonexistent/install returns 404."""
    response = await client.post(
        "/api/rules/packs/nonexistent/install",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Dashboard API — balance-history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_balance_history(client: AsyncClient, auth_headers):
    """GET /api/dashboard/balance-history returns current month and baseline data."""
    # Create an account first
    await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "BH Test", "type": "checking", "balance": 1000, "currency": "BRL"},
    )

    response = await client.get(
        "/api/dashboard/balance-history", headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "current" in data
    assert "opening_balance" in data
    assert "baseline" in data
    assert isinstance(data["current"], list)
    assert len(data["baseline"]) == len(data["current"])


@pytest.mark.asyncio
async def test_dashboard_balance_history_accepts_comparison_months(
    client: AsyncClient, auth_headers
):
    response = await client.get(
        "/api/dashboard/balance-history",
        params=[
            ("month", "2026-04-01"),
            ("comparison_months", "2026-01-01"),
            ("comparison_months", "2026-03-01"),
        ],
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["comparison"] is None
    assert len(data["baseline"]) == len(data["current"])

    months_response = await client.get(
        "/api/dashboard/balance-history/months", headers=auth_headers
    )
    assert months_response.status_code == 200
    assert isinstance(months_response.json(), list)


@pytest.mark.asyncio
async def test_dashboard_balance_history_rejects_non_prior_comparison_month(
    client: AsyncClient, auth_headers
):
    response = await client.get(
        "/api/dashboard/balance-history",
        params=[
            ("month", "2026-04-01"),
            ("comparison_months", "2026-04-01"),
        ],
        headers=auth_headers,
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Import API — additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_duplicate_skips(
    client: AsyncClient, auth_headers, test_account: Account,
):
    """Re-importing the same transactions skips duplicates."""
    transactions = [
        {
            "description": "Dup Test",
            "amount": "99.99",
            "date": "2026-01-15",
            "type": "debit",
        },
    ]

    # First import
    resp1 = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": transactions,
        },
    )
    assert resp1.status_code == 201
    assert resp1.json()["imported"] == 1

    # Second import — same data should be skipped
    resp2 = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": transactions,
        },
    )
    assert resp2.status_code == 201
    assert resp2.json()["imported"] == 0
    assert resp2.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_preview_qif_format(client: AsyncClient, auth_headers):
    """Preview QIF file."""
    qif_content = b"!Type:Bank\nD01/15/2026\nT-50.00\nPTest Payment\n^\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("data.qif", qif_content, "application/qif")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["detected_format"] == "qif"
    assert len(data["transactions"]) >= 1


@pytest.mark.asyncio
async def test_preview_csv_with_date_format(client: AsyncClient, auth_headers):
    """Preview CSV with explicit date format."""
    csv_content = b"date,description,amount\n03/15/2026,US Format,-30.00\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("us.csv", csv_content, "text/csv")},
        data={"date_format": "MM/DD/YYYY"},
    )
    assert response.status_code == 200
    data = response.json()
    assert [t["date"] for t in data["transactions"]] == ["2026-03-15"]


@pytest.mark.asyncio
async def test_preview_csv_with_flip_amount(client: AsyncClient, auth_headers):
    """Preview CSV with flip_amount option."""
    csv_content = b"date,description,amount\n2026-01-15,Inverted,50.00\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("flip.csv", csv_content, "text/csv")},
        data={"flip_amount": "true"},
    )
    assert response.status_code == 200
    data = response.json()
    # Amount was positive, flip makes it negative -> debit
    assert data["transactions"][0]["type"] == "debit"


@pytest.mark.asyncio
async def test_preview_csv_split_columns(client: AsyncClient, auth_headers):
    """Preview CSV with inflow/outflow split columns."""
    csv_content = b"date,description,inflow,outflow\n2026-01-15,Salary,5000,\n2026-01-16,Rent,,1500\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("split.csv", csv_content, "text/csv")},
        data={"inflow_column": "inflow", "outflow_column": "outflow"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["transactions"]) == 2
    assert data["transactions"][0]["type"] == "credit"
    assert data["transactions"][1]["type"] == "debit"
