"""Tests for loan prepayment API endpoints."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule


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
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)

    # Add schedule entries
    opening = Decimal("100000.00")
    for i in range(1, 13):
        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            account_id=account.id,
            schedule_version=1,
            emi_number=i,
            due_date=date(2026, 9, 1),
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

    return account


@pytest.mark.asyncio
async def test_simulate_prepayment(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test simulating prepayment options."""
    response = await client.post(
        "/api/v1/prepayments/simulate",
        json={
            "account_id": str(loan_account.id),
            "prepayment_amount": "10000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()

    # Should return both options
    assert "reduce_emi" in data
    assert "reduce_tenure" in data

    reduce_emi = data["reduce_emi"]
    assert Decimal(reduce_emi["new_emi_amount"]) < Decimal("3000.00")
    assert reduce_emi["new_tenure_months"] == 12
    assert Decimal(reduce_emi["total_interest_saved"]) > 0

    reduce_tenure = data["reduce_tenure"]
    assert Decimal(reduce_tenure["new_emi_amount"]) == Decimal("3000.00")
    assert reduce_tenure["new_tenure_months"] < 12
    assert Decimal(reduce_tenure["total_interest_saved"]) > 0


@pytest.mark.asyncio
async def test_record_prepayment_reduce_emi(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test recording prepayment with reduce_emi method."""
    response = await client.post(
        "/api/v1/prepayments",
        json={
            "account_id": str(loan_account.id),
            "prepayment_amount": "10000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
            "recalculation_method": "reduce_emi",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recalculation_method"] == "reduce_emi"
    assert Decimal(data["prepayment_amount"]) == Decimal("10000.00")
    assert data["schedule_version_before"] == 1
    assert data["schedule_version_after"] == 2
    assert Decimal(data["emi_change_amount"]) < 0  # EMI reduced


@pytest.mark.asyncio
async def test_record_prepayment_reduce_tenure(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test recording prepayment with reduce_tenure method."""
    response = await client.post(
        "/api/v1/prepayments",
        json={
            "account_id": str(loan_account.id),
            "prepayment_amount": "10000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
            "recalculation_method": "reduce_tenure",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recalculation_method"] == "reduce_tenure"
    assert data["tenure_change_months"] < 0  # Tenure reduced
    assert Decimal(data["emi_change_amount"]) == Decimal("0.00")  # EMI unchanged


@pytest.mark.asyncio
async def test_list_prepayments(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test listing prepayment history."""
    # Record a prepayment first
    await client.post(
        "/api/v1/prepayments",
        json={
            "account_id": str(loan_account.id),
            "prepayment_amount": "10000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
            "recalculation_method": "reduce_emi",
        },
        headers=auth_headers,
    )

    # List prepayments
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/prepayments",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert Decimal(data[0]["prepayment_amount"]) == Decimal("10000.00")


@pytest.mark.asyncio
async def test_simulate_prepayment_invalid_amount(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test simulating prepayment with invalid amount."""
    response = await client.post(
        "/api/v1/prepayments/simulate",
        json={
            "account_id": str(loan_account.id),
            "prepayment_amount": "-1000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_record_prepayment_nonexistent_account(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test recording prepayment for non-existent account."""
    response = await client.post(
        "/api/v1/prepayments",
        json={
            "account_id": str(uuid.uuid4()),
            "prepayment_amount": "10000.00",
            "annual_interest_rate": "9.0",
            "current_emi_number": 3,
            "recalculation_method": "reduce_emi",
        },
        headers=auth_headers,
    )

    assert response.status_code == 404
