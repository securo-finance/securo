"""Combined scenario math + commitment wiring tests."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.loan_plan_commitment import LoanPlanCommitment
from app.models.recurring_transaction import RecurringTransaction
from app.services.loan_combined_simulation_service import (
    ScenarioEvent,
    summarize,
    walk_amortization,
)
from app.services.loan_schedule_service import calculate_emi, generate_amortization_schedule


def test_walk_recurring_prepay_saves_months_and_interest():
    principal = Decimal("100000.00")
    rate = Decimal("6.00")
    tenure = 120
    emi = calculate_emi(principal, rate, tenure)
    start = date(2026, 1, 1)

    baseline = walk_amortization(
        principal=principal,
        annual_rate=rate,
        emi=emi,
        start_date=start,
        emi_day=1,
        events=[],
    )
    scenario = walk_amortization(
        principal=principal,
        annual_rate=rate,
        emi=emi,
        start_date=start,
        emi_day=1,
        events=[
            ScenarioEvent(
                type="recurring_prepayment",
                date=date(2026, 2, 1),
                amount=Decimal("200.00"),
                months=60,
            ),
            ScenarioEvent(
                type="one_time_prepayment",
                date=date(2026, 6, 1),
                amount=Decimal("5000.00"),
            ),
            ScenarioEvent(
                type="rate_change",
                date=date(2027, 1, 1),
                new_rate=Decimal("5.50"),
            ),
        ],
        strategy="reduce_tenure",
    )
    base = summarize(baseline, emi, rate)
    scen = summarize(scenario, emi, rate)
    assert scen["months"] < base["months"]
    assert scen["total_interest"] < base["total_interest"]
    assert scenario[-1].closing == Decimal("0.00") or scenario[-1].closing < Decimal("1.00")


def test_emi_holiday_accrues_interest_without_principal():
    principal = Decimal("10000.00")
    rate = Decimal("12.00")
    emi = calculate_emi(principal, rate, 24)
    steps = walk_amortization(
        principal=principal,
        annual_rate=rate,
        emi=emi,
        start_date=date(2026, 1, 1),
        emi_day=1,
        events=[ScenarioEvent(type="emi_holiday", date=date(2026, 2, 1), months=1)],
    )
    holiday = next(s for s in steps if s.holiday)
    assert holiday.principal == Decimal("0.00")
    assert holiday.interest > 0


@pytest.mark.asyncio
async def test_combined_simulation_route(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
    test_workspace,
    auth_headers: dict,
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Combined Sim Loan",
        type="loan",
        balance=Decimal("50000.00"),
        currency="USD",
        loan_kind="personal",
        original_principal=Decimal("50000.00"),
        interest_rate=Decimal("8.00"),
        tenure_months=60,
        disbursed_on=date(2025, 1, 1),
        emi_day=1,
    )
    session.add(account)
    await session.commit()
    await generate_amortization_schedule(session, account.id, version=1)

    response = await client.post(
        "/api/v1/loans/simulations/combined",
        json={
            "account_id": str(account.id),
            "strategy": "reduce_tenure",
            "events": [
                {
                    "type": "recurring_prepayment",
                    "date": "2026-10-01",
                    "amount": "150.00",
                    "months": 24,
                },
                {"type": "rate_change", "date": "2027-01-01", "new_rate": "7.5"},
            ],
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "kpis" in data
    assert data["kpis"]["months_saved"] >= 0
    assert data["kpis"]["interest_saved"] >= 0
    assert len(data["timeline"]) > 0
    assert len(data["curves"]["outstanding"]) == len(data["curves"]["dates"])


@pytest.mark.asyncio
async def test_commit_recurring_creates_budget_and_recurring(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
    test_workspace,
    auth_headers: dict,
):
    checking = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Test Checking",
        type="checking",
        balance=Decimal("5000.00"),
        currency="USD",
    )
    loan = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Commit Loan",
        type="loan",
        balance=Decimal("20000.00"),
        currency="USD",
        loan_kind="auto",
        original_principal=Decimal("20000.00"),
        interest_rate=Decimal("5.90"),
        tenure_months=48,
        disbursed_on=date(2025, 6, 1),
        emi_day=15,
    )
    cat = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="HousingTest",
        icon="home",
        color="#000",
    )
    session.add_all([checking, loan, cat])
    await session.commit()

    response = await client.post(
        "/api/v1/loans/commitments",
        json={
            "account_id": str(loan.id),
            "kind": "recurring_prepayment",
            "amount": "200.00",
            "start_date": "2026-09-15",
            "day_of_month": 15,
            "funding_account_id": str(checking.id),
            "category_id": str(cat.id),
            "notes": "test commit",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["budget_id"] is not None
    assert body["recurring_transaction_id"] is not None

    budget = await session.get(Budget, uuid.UUID(body["budget_id"]))
    assert budget is not None
    assert budget.amount == Decimal("200.00")
    assert budget.is_recurring is True

    recurring = await session.get(RecurringTransaction, uuid.UUID(body["recurring_transaction_id"]))
    assert recurring is not None
    assert recurring.amount == Decimal("200.00")
    assert recurring.is_active is True
    assert recurring.frequency == "monthly"

    listed = await client.get(
        f"/api/v1/loans/commitments?account_id={loan.id}",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

    cancel = await client.post(
        f"/api/v1/loans/commitments/{body['id']}/cancel",
        headers=auth_headers,
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    await session.refresh(recurring)
    assert recurring.is_active is False
