"""Tests for loan schedule retrieval API endpoints."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule


@pytest_asyncio.fixture
async def loan_account(db_session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Account:
    """Create a test loan account."""
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
    return account


@pytest_asyncio.fixture
async def schedule_entries(
    db_session: AsyncSession, loan_account: Account, workspace_id: uuid.UUID
) -> list[LoanAmortizationSchedule]:
    """Create test schedule entries."""
    entries = []
    base_date = date(2026, 9, 1)
    opening = Decimal("100000.00")

    for i in range(1, 4):
        entry = LoanAmortizationSchedule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            account_id=loan_account.id,
            schedule_version=1,
            emi_number=i,
            due_date=base_date + timedelta(days=30 * (i - 1)),
            principal_component=Decimal("2000.00"),
            interest_component=Decimal("1000.00"),
            emi_amount=Decimal("3000.00"),
            opening_balance=opening,
            closing_balance=opening - Decimal("2000.00"),
            payment_status="scheduled" if i == 1 else "paid",
        )
        opening -= Decimal("2000.00")
        entries.append(entry)
        db_session.add(entry)

    await db_session.commit()
    return entries


@pytest.mark.asyncio
async def test_get_schedule_all_entries(
    client: AsyncClient,
    loan_account: Account,
    schedule_entries: list[LoanAmortizationSchedule],
    auth_headers: dict,
):
    """Test retrieving all schedule entries for a loan."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/schedule",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["emi_number"] == 1
    assert data[0]["payment_status"] == "scheduled"
    assert data[1]["payment_status"] == "paid"


@pytest.mark.asyncio
async def test_get_schedule_filter_by_status(
    client: AsyncClient,
    loan_account: Account,
    schedule_entries: list[LoanAmortizationSchedule],
    auth_headers: dict,
):
    """Test filtering schedule by payment status."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/schedule?status=paid",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(e["payment_status"] == "paid" for e in data)


@pytest.mark.asyncio
async def test_get_schedule_filter_by_date_range(
    client: AsyncClient,
    loan_account: Account,
    schedule_entries: list[LoanAmortizationSchedule],
    auth_headers: dict,
):
    """Test filtering schedule by date range."""
    start_date = date(2026, 9, 15)
    end_date = date(2026, 10, 15)

    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/schedule"
        f"?from_date={start_date.isoformat()}&to_date={end_date.isoformat()}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["emi_number"] == 2


@pytest.mark.asyncio
async def test_export_schedule_csv(
    client: AsyncClient,
    loan_account: Account,
    schedule_entries: list[LoanAmortizationSchedule],
    auth_headers: dict,
):
    """Test exporting schedule as CSV."""
    response = await client.get(
        f"/api/v1/loans/{loan_account.id}/schedule/export",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"

    csv_content = response.text
    lines = csv_content.strip().split("\n")
    assert len(lines) == 4  # header + 3 entries
    assert "EMI Number" in lines[0]
    assert "Due Date" in lines[0]
    assert "Principal" in lines[0]


@pytest.mark.asyncio
async def test_get_schedule_nonexistent_account(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test retrieving schedule for non-existent account."""
    response = await client.get(
        f"/api/v1/loans/{uuid.uuid4()}/schedule",
        headers=auth_headers,
    )

    assert response.status_code == 404
