"""Integration tests for complete loan amortization flow."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account


@pytest.mark.asyncio
async def test_complete_loan_lifecycle(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
    user_id: uuid.UUID,
):
    """Test complete loan lifecycle from creation to closure."""

    # Step 1: Create loan account
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Home Loan",
        type="loan",
        user_id=user_id,
        balance=Decimal("-1000000.00"),
        currency="INR",
        current_schedule_version=0,
    )
    db_session.add(account)
    await db_session.commit()
    account_id = str(account.id)

    # Step 2: Generate initial schedule
    response = await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": account_id,
            "from_emi_number": 1,
            "new_principal": "1000000.00",
            "new_annual_rate": "8.5",
            "new_tenure_months": 240,
            "start_date": "2026-01-01",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    schedule_data = response.json()
    assert schedule_data["new_schedule_version"] == 1
    assert schedule_data["entries_created"] == 240

    # Step 3: Get schedule
    response = await client.get(
        f"/api/v1/loans/{account_id}/schedule",
        headers=auth_headers,
    )
    assert response.status_code == 200
    schedule = response.json()
    assert len(schedule) == 240

    # Step 4: Get loan overview
    response = await client.get(
        f"/api/v1/loans/{account_id}/overview",
        headers=auth_headers,
    )
    assert response.status_code == 200
    overview = response.json()
    assert overview["emis_paid"] == 0
    assert overview["emis_remaining"] == 240

    # Step 5: Mark first EMI as paid
    first_entry_id = schedule[0]["id"]
    response = await client.put(
        f"/api/v1/loans/schedule/{first_entry_id}/status",
        json={"payment_status": "paid"},
        headers=auth_headers,
    )
    assert response.status_code == 200

    # Step 6: Simulate prepayment
    response = await client.post(
        "/api/v1/prepayments/simulate",
        json={
            "account_id": account_id,
            "prepayment_amount": "100000.00",
            "annual_interest_rate": "8.5",
            "current_emi_number": 2,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    simulation = response.json()
    assert "reduce_emi" in simulation
    assert "reduce_tenure" in simulation

    # Step 7: Record prepayment with reduce_tenure
    response = await client.post(
        "/api/v1/prepayments",
        json={
            "account_id": account_id,
            "prepayment_amount": "100000.00",
            "annual_interest_rate": "8.5",
            "current_emi_number": 2,
            "recalculation_method": "reduce_tenure",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    prepayment = response.json()
    assert prepayment["schedule_version_after"] == 2
    assert prepayment["tenure_change_months"] < 0  # Tenure reduced

    # Step 8: Verify new schedule version created
    response = await client.get(
        f"/api/v1/loans/{account_id}/schedule",
        headers=auth_headers,
    )
    assert response.status_code == 200
    new_schedule = response.json()
    assert len(new_schedule) < 240  # Tenure reduced

    # Step 9: Get updated overview
    response = await client.get(
        f"/api/v1/loans/{account_id}/overview",
        headers=auth_headers,
    )
    assert response.status_code == 200
    updated_overview = response.json()
    assert Decimal(updated_overview["total_prepayments"]) == Decimal("100000.00")

    # Step 10: Get dashboard summary
    response = await client.get(
        "/api/v1/loans/dashboard",
        headers=auth_headers,
    )
    assert response.status_code == 200
    dashboard = response.json()
    assert len(dashboard["next_due_payments"]) > 0


@pytest.mark.asyncio
async def test_bulk_operations_workflow(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
    user_id: uuid.UUID,
):
    """Test bulk operations across multiple loans."""

    # Create multiple loan accounts with schedules
    account_ids = []
    for i in range(3):
        account = Account(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=f"Loan {i+1}",
            type="loan",
            user_id=user_id,
            balance=Decimal("-100000.00"),
            currency="INR",
            current_schedule_version=1,
        )
        db_session.add(account)
        await db_session.commit()
        account_ids.append(str(account.id))

        # Generate schedule for each
        await client.post(
            "/api/v1/loans/schedule/regenerate",
            json={
                "account_id": str(account.id),
                "from_emi_number": 1,
                "new_principal": "100000.00",
                "new_annual_rate": "9.0",
                "new_tenure_months": 12,
                "start_date": "2026-09-01",
            },
            headers=auth_headers,
        )

    # Bulk export all schedules
    response = await client.post(
        "/api/v1/loans/schedule/bulk-export",
        json={"account_ids": account_ids},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    # Get workspace summary
    response = await client.get(
        "/api/v1/loans/summary",
        headers=auth_headers,
    )
    assert response.status_code == 200
    summary = response.json()
    assert summary["total_loans"] >= 3


@pytest.mark.asyncio
async def test_schedule_validation_workflow(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
    user_id: uuid.UUID,
):
    """Test schedule validation after manual edits."""

    # Create account with schedule
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test Loan",
        type="loan",
        user_id=user_id,
        balance=Decimal("-100000.00"),
        currency="INR",
        current_schedule_version=1,
    )
    db_session.add(account)
    await db_session.commit()

    # Generate schedule
    await client.post(
        "/api/v1/loans/schedule/regenerate",
        json={
            "account_id": str(account.id),
            "from_emi_number": 1,
            "new_principal": "100000.00",
            "new_annual_rate": "9.0",
            "new_tenure_months": 12,
            "start_date": "2026-09-01",
        },
        headers=auth_headers,
    )

    # Get schedule entries
    response = await client.get(
        f"/api/v1/loans/{account.id}/schedule",
        headers=auth_headers,
    )
    schedule = response.json()

    # Update first entry
    first_entry = schedule[0]
    await client.patch(
        f"/api/v1/loans/schedule/{first_entry['id']}",
        json={"emi_amount": "9000.00"},
        headers=auth_headers,
    )

    # Validate schedule integrity
    response = await client.post(
        "/api/v1/loans/validate-schedule",
        json={"account_id": str(account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 200
    validation = response.json()
    assert "is_valid" in validation
    assert "issues" in validation
