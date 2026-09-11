"""Tests for loan analytics API endpoints."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule
from app.models.loan_prepayment import LoanPrepayment


@pytest_asyncio.fixture
async def loan_account(db_session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Account:
    """Create a test loan account with schedule."""
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test Loan",
        type="loan",
        user_id=user_id,
        balance=Decimal("-90000.00"),
        currency="INR",
        current_schedule_version=1,
        last_payment_date=date(2026, 8, 1),
        total_prepayments=Decimal("5000.00"),
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)

    # Add schedule entries
    opening = Decimal("100000.00")
    for i in range(1, 13):
        status = "paid" if i <= 2 else "scheduled"
        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            account_id=account.id,
            schedule_version=1,
            emi_number=i,
            due_date=date(2026, i, 1),
            principal_component=Decimal("2000.00"),
            interest_component=Decimal("1000.00"),
            emi_amount=Decimal("3000.00"),
            opening_balance=opening,
            closing_balance=opening - Decimal("2000.00"),
            payment_status=status,
        )
        opening -= Decimal("2000.00")
        db_session.add(entry)
    await db_session.commit()

    return account


@pytest.mark.asyncio
async def test_get_loan_overview(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test getting loan overview metrics."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/overview",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "progress_percent" in data
    assert "emis_paid" in data
    assert "emis_remaining" in data
    assert "principal_paid" in data
    assert "principal_remaining" in data
    assert "interest_paid" in data
    assert "interest_remaining" in data
    assert "total_prepayments" in data

    assert data["emis_paid"] == 2
    assert data["emis_remaining"] == 10
    assert Decimal(data["total_prepayments"]) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_get_yearly_breakdown(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test getting yearly breakdown of payments."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/breakdown?group_by=year",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert len(data) > 0
    first_year = data[0]
    assert "period" in first_year
    assert "principal_paid" in first_year
    assert "interest_paid" in first_year
    assert "total_paid" in first_year
    assert "prepayments" in first_year


@pytest.mark.asyncio
async def test_get_monthly_breakdown(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test getting monthly breakdown of payments."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/breakdown?group_by=month",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert len(data) >= 2  # At least 2 paid entries
    january = next((m for m in data if "2026-01" in m["period"]), None)
    assert january is not None
    assert Decimal(january["principal_paid"]) == Decimal("2000.00")
    assert Decimal(january["interest_paid"]) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_calculate_debt_ratios(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test calculating debt-to-income ratios."""
    response = await client.get(
        f"/api/v1/loans/debt-ratios?monthly_income=50000.00",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "total_monthly_emi" in data
    assert "debt_to_income_ratio" in data
    assert "emi_to_income_ratio" in data
    assert "total_outstanding" in data
    assert "weighted_avg_interest_rate" in data
    assert "health_status" in data

    assert Decimal(data["total_monthly_emi"]) == Decimal("3000.00")
    assert data["health_status"] in ["healthy", "moderate", "high_risk"]


@pytest.mark.asyncio
async def test_get_dashboard_summary(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test getting dashboard summary with alerts."""
    response = await client.get(
        "/api/v1/loans/dashboard",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "next_due_payments" in data
    assert "recent_payments" in data
    assert "alerts" in data
    assert "total_monthly_emi" in data
    assert "total_outstanding" in data

    # Check next due payments structure
    if len(data["next_due_payments"]) > 0:
        payment = data["next_due_payments"][0]
        assert "account_name" in payment
        assert "due_date" in payment
        assert "emi_amount" in payment
        assert "days_until_due" in payment


@pytest.mark.asyncio
async def test_get_overview_nonexistent_account(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test getting overview for non-existent account."""
    response = await client.get(
        f"/api/v1/loans/{uuid.uuid4()}/overview",
        headers=auth_headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_debt_ratios_without_income(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test debt ratios endpoint without monthly income."""
    response = await client.get(
        "/api/v1/loans/debt-ratios",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    # Should still return metrics, but ratios may be null or 0
    assert "total_monthly_emi" in data
    assert "total_outstanding" in data
