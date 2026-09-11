"""Tests for loan bulk operations API endpoint."""
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
async def loan_accounts(db_session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> list[Account]:
    """Create multiple test loan accounts with schedules."""
    accounts = []
    for i in range(3):
        account = Account(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=f"Test Loan {i+1}",
            type="loan",
            user_id=user_id,
            balance=Decimal("-50000.00"),
            currency="INR",
            current_schedule_version=1,
        )
        db_session.add(account)
        await db_session.commit()
        await db_session.refresh(account)

        # Add schedule entries
        for j in range(1, 6):
            entry = LoanAmortizationSchedule(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                account_id=account.id,
                schedule_version=1,
                emi_number=j,
                due_date=date(2026, 9, j),
                principal_component=Decimal("1000.00"),
                interest_component=Decimal("500.00"),
                emi_amount=Decimal("1500.00"),
                opening_balance=Decimal("50000.00"),
                closing_balance=Decimal("49000.00"),
                payment_status="scheduled",
            )
            db_session.add(entry)
        await db_session.commit()
        accounts.append(account)

    return accounts


@pytest.mark.asyncio
async def test_bulk_mark_status_multiple_entries(
    client: AsyncClient,
    loan_accounts: list[Account],
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
):
    """Test bulk marking status for multiple schedule entries."""
    # Get entry IDs for first 2 accounts
    from app.models.loan_schedule import LoanAmortizationSchedule
    from sqlalchemy import select

    result = await db_session.execute(
        select(LoanAmortizationSchedule.id)
        .where(
            LoanAmortizationSchedule.account_id.in_([loan_accounts[0].id, loan_accounts[1].id]),
            LoanAmortizationSchedule.emi_number == 1,
        )
    )
    entry_ids = [str(row[0]) for row in result.all()]

    response = await client.post(
        "/api/v1/loans/schedule/bulk-mark-status",
        json={
            "entry_ids": entry_ids,
            "payment_status": "paid",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["updated_count"] == 2


@pytest.mark.asyncio
async def test_bulk_delete_schedules(
    client: AsyncClient,
    loan_accounts: list[Account],
    auth_headers: dict,
):
    """Test bulk deletion of loan schedules (for cleanup/reset)."""
    response = await client.post(
        "/api/v1/loans/schedule/bulk-delete",
        json={
            "account_ids": [str(loan_accounts[0].id), str(loan_accounts[1].id)],
            "schedule_version": 1,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deleted_count"] >= 2


@pytest.mark.asyncio
async def test_bulk_export_multiple_loans(
    client: AsyncClient,
    loan_accounts: list[Account],
    auth_headers: dict,
):
    """Test bulk exporting schedules for multiple loans as ZIP."""
    response = await client.post(
        "/api/v1/loans/schedule/bulk-export",
        json={
            "account_ids": [str(acc.id) for acc in loan_accounts],
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


@pytest.mark.asyncio
async def test_bulk_operations_empty_list(
    client: AsyncClient,
    auth_headers: dict,
):
    """Test bulk operations with empty entry list."""
    response = await client.post(
        "/api/v1/loans/schedule/bulk-mark-status",
        json={
            "entry_ids": [],
            "payment_status": "paid",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["updated_count"] == 0


@pytest.mark.asyncio
async def test_bulk_mark_status_invalid_status(
    client: AsyncClient,
    loan_accounts: list[Account],
    auth_headers: dict,
):
    """Test bulk marking with invalid status."""
    response = await client.post(
        "/api/v1/loans/schedule/bulk-mark-status",
        json={
            "entry_ids": [str(uuid.uuid4())],
            "payment_status": "invalid_status",
        },
        headers=auth_headers,
    )

    assert response.status_code == 422
