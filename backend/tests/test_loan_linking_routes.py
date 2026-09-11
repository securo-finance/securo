"""Tests for loan transaction linking API endpoints."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_schedule import LoanAmortizationSchedule
from app.models.transaction import Transaction


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


@pytest.fixture
async def payment_transaction(
    db_session: AsyncSession, loan_account: Account, workspace_id: uuid.UUID
) -> Transaction:
    """Create a test payment transaction."""
    transaction = Transaction(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        account_id=loan_account.id,
        date=date(2026, 9, 1),
        amount=Decimal("-3000.00"),
        description="Loan EMI Payment",
        currency="INR",
    )
    db_session.add(transaction)
    await db_session.commit()
    await db_session.refresh(transaction)
    return transaction


@pytest.mark.asyncio
async def test_auto_link_transactions_exact_match(
    client: AsyncClient,
    loan_account: Account,
    schedule_entry: LoanAmortizationSchedule,
    payment_transaction: Transaction,
    auth_headers: dict,
):
    """Test auto-linking with exact date and amount match."""
    response = await client.post(
        "/api/v1/loans/schedule/auto-link",
        json={
            "account_id": str(loan_account.id),
            "date_tolerance_days": 3,
            "amount_tolerance_percent": "2.0",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["linked_count"] >= 1
    assert len(data["matches"]) >= 1

    match = data["matches"][0]
    assert match["confidence"] == "exact"
    assert match["schedule_entry_id"] == str(schedule_entry.id)
    assert match["transaction_id"] == str(payment_transaction.id)


@pytest.mark.asyncio
async def test_auto_link_transactions_high_confidence(
    client: AsyncClient,
    loan_account: Account,
    schedule_entry: LoanAmortizationSchedule,
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    auth_headers: dict,
):
    """Test auto-linking with high confidence (date within tolerance)."""
    # Create transaction 2 days after due date
    transaction = Transaction(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        account_id=loan_account.id,
        date=date(2026, 9, 3),
        amount=Decimal("-3000.00"),
        description="Loan Payment",
        currency="INR",
    )
    db_session.add(transaction)
    await db_session.commit()

    response = await client.post(
        "/api/v1/loans/schedule/auto-link",
        json={
            "account_id": str(loan_account.id),
            "date_tolerance_days": 3,
            "amount_tolerance_percent": "2.0",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["linked_count"] >= 1

    match = next((m for m in data["matches"] if m["transaction_id"] == str(transaction.id)), None)
    assert match is not None
    assert match["confidence"] == "high"


@pytest.mark.asyncio
async def test_manual_link_transaction(
    client: AsyncClient,
    schedule_entry: LoanAmortizationSchedule,
    payment_transaction: Transaction,
    auth_headers: dict,
):
    """Test manually linking a transaction to schedule entry."""
    response = await client.post(
        f"/api/v1/loans/schedule/{schedule_entry.id}/link",
        json={"transaction_id": str(payment_transaction.id)},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["linked_transaction_id"] == str(payment_transaction.id)
    assert data["payment_status"] == "paid"


@pytest.mark.asyncio
async def test_manual_link_nonexistent_entry(
    client: AsyncClient,
    payment_transaction: Transaction,
    auth_headers: dict,
):
    """Test manually linking to non-existent schedule entry."""
    response = await client.post(
        f"/api/v1/loans/schedule/{uuid.uuid4()}/link",
        json={"transaction_id": str(payment_transaction.id)},
        headers=auth_headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_auto_link_no_matches(
    client: AsyncClient,
    loan_account: Account,
    schedule_entry: LoanAmortizationSchedule,
    auth_headers: dict,
):
    """Test auto-linking when no transactions match."""
    response = await client.post(
        "/api/v1/loans/schedule/auto-link",
        json={
            "account_id": str(loan_account.id),
            "date_tolerance_days": 1,
            "amount_tolerance_percent": "1.0",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["linked_count"] == 0
    assert len(data["matches"]) == 0
