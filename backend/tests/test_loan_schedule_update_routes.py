"""Tests for loan schedule update API endpoints."""
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
async def schedule_entry(
    db_session: AsyncSession, loan_account: Account, workspace_id: uuid.UUID
) -> LoanAmortizationSchedule:
    """Create a test schedule entry."""
    entry = LoanAmortizationSchedule(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        account_id=loan_account.id,
        schedule_version=1,
        emi_number=1,
        due_date=date(2026, 9, 1),
        principal_component=Decimal("2000.00"),
        interest_component=Decimal("1000.00"),
        emi_amount=Decimal("3000.00"),
        opening_balance=Decimal("100000.00"),
        closing_balance=Decimal("98000.00"),
        payment_status="scheduled",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_update_schedule_entry_due_date(
    client: AsyncClient,
    schedule_entry: LoanAmortizationSchedule,
    auth_headers: dict,
):
    """Test updating a schedule entry's due date."""
    new_date = date(2026, 9, 5)
    response = await client.patch(
        f"/api/v1/loans/schedule/{schedule_entry.id}",
        json={"due_date": new_date.isoformat()},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["due_date"] == new_date.isoformat()


@pytest.mark.asyncio
async def test_update_schedule_entry_emi_amount(
    client: AsyncClient,
    schedule_entry: LoanAmortizationSchedule,
    auth_headers: dict,
):
    """Test updating a schedule entry's EMI amount."""
    response = await client.patch(
        f"/api/v1/loans/schedule/{schedule_entry.id}",
        json={"emi_amount": "3500.00"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["emi_amount"]) == Decimal("3500.00")


@pytest.mark.asyncio
async def test_bulk_update_dates_shift(
    client: AsyncClient,
    loan_account: Account,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
):
    """Test bulk shifting of due dates."""
    # Create multiple entries
    base_date = date(2026, 9, 1)
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
            opening_balance=Decimal("100000.00") - Decimal("2000.00") * (i - 1),
            closing_balance=Decimal("100000.00") - Decimal("2000.00") * i,
            payment_status="scheduled",
        )
        db_session.add(entry)
    await db_session.commit()

    response = await client.post(
        "/api/v1/loans/schedule/bulk-update-dates",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 2,
            "shift_days": 5,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["updated_count"] == 2


@pytest.mark.asyncio
async def test_bulk_update_dates_change_day(
    client: AsyncClient,
    loan_account: Account,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
):
    """Test bulk changing EMI day of month."""
    base_date = date(2026, 9, 1)
    for i in range(1, 3):
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
            opening_balance=Decimal("100000.00") - Decimal("2000.00") * (i - 1),
            closing_balance=Decimal("100000.00") - Decimal("2000.00") * i,
            payment_status="scheduled",
        )
        db_session.add(entry)
    await db_session.commit()

    response = await client.post(
        "/api/v1/loans/schedule/bulk-update-dates",
        json={
            "account_id": str(loan_account.id),
            "from_emi_number": 1,
            "new_day_of_month": 15,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["updated_count"] == 2


@pytest.mark.asyncio
async def test_mark_entry_status(
    client: AsyncClient,
    schedule_entry: LoanAmortizationSchedule,
    auth_headers: dict,
):
    """Test marking a schedule entry's payment status."""
    response = await client.put(
        f"/api/v1/loans/schedule/{schedule_entry.id}/status",
        json={"payment_status": "paid"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["payment_status"] == "paid"


@pytest.mark.asyncio
async def test_mark_entry_status_invalid(
    client: AsyncClient,
    schedule_entry: LoanAmortizationSchedule,
    auth_headers: dict,
):
    """Test marking with invalid status."""
    response = await client.put(
        f"/api/v1/loans/schedule/{schedule_entry.id}/status",
        json={"payment_status": "invalid_status"},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_nonexistent_entry(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test updating non-existent schedule entry."""
    response = await client.patch(
        f"/api/v1/loans/schedule/{uuid.uuid4()}",
        json={"due_date": "2026-09-15"},
        headers=auth_headers,
    )

    assert response.status_code == 404
