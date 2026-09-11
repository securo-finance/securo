"""Tests for loan utility API endpoints."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule


@pytest.mark.asyncio
async def test_calculate_emi(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test EMI calculation utility."""
    response = await client.post(
        "/api/v1/loans/calculate-emi",
        json={
            "principal": "100000.00",
            "annual_rate": "9.0",
            "tenure_months": 12,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "emi_amount" in data
    assert "total_interest" in data
    assert "total_payment" in data
    assert Decimal(data["emi_amount"]) > Decimal("8000.00")


@pytest.mark.asyncio
async def test_calculate_emi_zero_interest(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test EMI calculation with zero interest."""
    response = await client.post(
        "/api/v1/loans/calculate-emi",
        json={
            "principal": "120000.00",
            "annual_rate": "0.0",
            "tenure_months": 12,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["emi_amount"]) == Decimal("10000.00")
    assert Decimal(data["total_interest"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_validate_schedule_integrity(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
    user_id: uuid.UUID,
):
    """Test schedule integrity validation."""
    # Create account with schedule
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test Loan",
        type="loan",
        user_id=user_id,
        balance=Decimal("-90000.00"),
        currency="INR",
        current_schedule_version=1,
    )
    db_session.add(account)
    await db_session.commit()

    opening = Decimal("100000.00")
    for i in range(1, 5):
        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            account_id=account.id,
            schedule_version=1,
            emi_number=i,
            due_date=date(2026, 9, i),
            principal_component=Decimal("2000.00"),
            interest_component=Decimal("1000.00"),
            emi_amount=Decimal("3000.00"),
            opening_balance=opening,
            closing_balance=opening - Decimal("2000.00"),
            payment_status="scheduled",
        )
        opening -= Decimal("2000.00")
        db_session.add(entry)
    await db_session.commit()

    response = await client.post(
        "/api/v1/loans/validate-schedule",
        json={"account_id": str(account.id)},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "is_valid" in data
    assert "issues" in data


@pytest.mark.asyncio
async def test_get_loan_summary(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
    user_id: uuid.UUID,
):
    """Test getting summary of all loans in workspace."""
    # Create multiple loan accounts
    for i in range(2):
        account = Account(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=f"Loan {i+1}",
            type="loan",
            user_id=user_id,
            balance=Decimal("-50000.00"),
            currency="INR",
            current_schedule_version=1,
        )
        db_session.add(account)
    await db_session.commit()

    response = await client.get(
        "/api/v1/loans/summary",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "total_loans" in data
    assert "total_outstanding" in data
    assert "total_monthly_emi" in data
    assert data["total_loans"] >= 2


@pytest.mark.asyncio
async def test_calculate_prepayment_savings(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test prepayment savings calculator utility."""
    response = await client.post(
        "/api/v1/loans/calculate-savings",
        json={
            "remaining_principal": "80000.00",
            "annual_rate": "9.0",
            "remaining_months": 24,
            "prepayment_amount": "20000.00",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "interest_saved_reduce_emi" in data
    assert "interest_saved_reduce_tenure" in data
    assert "months_saved_reduce_tenure" in data


@pytest.mark.asyncio
async def test_calculate_emi_invalid_input(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test EMI calculation with invalid inputs."""
    response = await client.post(
        "/api/v1/loans/calculate-emi",
        json={
            "principal": "-100000.00",
            "annual_rate": "9.0",
            "tenure_months": 12,
        },
        headers=auth_headers,
    )

    assert response.status_code == 422
