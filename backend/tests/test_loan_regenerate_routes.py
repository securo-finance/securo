"""Tests for loan schedule regeneration API endpoint."""
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

    # Add initial schedule
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
async def test_regenerate_schedule_from_emi(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test regenerating schedule from a specific EMI number."""
    response = await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 6,
            "new_principal": "60000.00",
            "new_annual_rate": "8.5",
            "new_tenure_months": 10,
            "start_date": "2027-03-01",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["new_schedule_version"] == 2
    assert data["entries_created"] == 10


@pytest.mark.asyncio
async def test_regenerate_schedule_updates_account(
    client: AsyncClient,
    loan_account: Account,
    db_session: AsyncSession,
    auth_headers: dict,
):
    """Test that regeneration updates account's current version."""
    await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 5,
            "new_principal": "70000.00",
            "new_annual_rate": "9.0",
            "new_tenure_months": 12,
            "start_date": "2027-02-01",
        },
        headers=auth_headers,
    )

    # Refresh account from DB
    await db_session.refresh(loan_account)
    assert loan_account.current_schedule_version == 2


@pytest.mark.asyncio
async def test_regenerate_schedule_preserves_old_version(
    client: AsyncClient,
    loan_account: Account,
    db_session: AsyncSession,
    auth_headers: dict,
):
    """Test that old schedule entries are preserved."""
    # Get count of version 1 entries before regeneration
    from sqlalchemy import select, func
    from app.models.loan_schedule import LoanAmortizationSchedule

    result = await db_session.execute(
        select(func.count(LoanAmortizationSchedule.id)).where(
            LoanAmortizationSchedule.account_id == loan_account.id,
            LoanAmortizationSchedule.schedule_version == 1,
        )
    )
    count_before = result.scalar()

    # Regenerate
    await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 6,
            "new_principal": "60000.00",
            "new_annual_rate": "8.5",
            "new_tenure_months": 10,
            "start_date": "2027-03-01",
        },
        headers=auth_headers,
    )

    # Check version 1 entries still exist
    result = await db_session.execute(
        select(func.count(LoanAmortizationSchedule.id)).where(
            LoanAmortizationSchedule.account_id == loan_account.id,
            LoanAmortizationSchedule.schedule_version == 1,
        )
    )
    count_after = result.scalar()

    assert count_after == count_before


@pytest.mark.asyncio
async def test_regenerate_schedule_invalid_emi_number(
    client: AsyncClient,
    loan_account: Account,
    auth_headers: dict,
):
    """Test regenerating from invalid EMI number."""
    response = await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 0,
            "new_principal": "60000.00",
            "new_annual_rate": "8.5",
            "new_tenure_months": 10,
            "start_date": "2027-03-01",
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_regenerate_schedule_nonexistent_account(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test regenerating schedule for non-existent account."""
    response = await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(uuid.uuid4()),
            "from_emi_number": 6,
            "new_principal": "60000.00",
            "new_annual_rate": "8.5",
            "new_tenure_months": 10,
            "start_date": "2027-03-01",
        },
        headers=auth_headers,
    )

    assert response.status_code == 404
