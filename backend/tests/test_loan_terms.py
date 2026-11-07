import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.loan_schedule import add_months

TERMS = {
    "loan_kind": "personal",
    "loan_original_amount": 10000,
    "loan_interest_rate": 1,
    "loan_rate_period": "monthly",
    "loan_term_months": 12,
    "loan_amortization": "price",
}


def _first_payment(months_ago: int) -> str:
    return add_months(app_today().replace(day=1), -months_ago).replace(day=10).isoformat()


async def _loan(client: AsyncClient, headers: dict, **overrides) -> dict:
    body = {
        "name": "Personal loan",
        "type": "loan",
        "balance": 10000,
        "loan_first_payment_date": _first_payment(0),
        **TERMS,
        **overrides,
    }
    response = await client.post("/api/accounts", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_loan_keeps_its_contract_terms(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)

    fetched = (await client.get(f"/api/accounts/{loan['id']}", headers=auth_headers)).json()

    assert fetched["loan_kind"] == "personal"
    assert fetched["loan_original_amount"] == 10000
    assert fetched["loan_interest_rate"] == 1
    assert fetched["loan_rate_period"] == "monthly"
    assert fetched["loan_term_months"] == 12
    assert fetched["loan_amortization"] == "price"


@pytest.mark.asyncio
async def test_the_schedule_follows_the_contract(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)

    schedule = await client.get(f"/api/accounts/{loan['id']}/loan-schedule", headers=auth_headers)

    assert schedule.status_code == 200, schedule.text
    body = schedule.json()
    assert body["configured"] is True
    assert body["monthly_payment"] == 888.49
    assert len(body["installments"]) == 12
    assert body["installments"][-1]["balance"] == 0
    assert body["current_debt"] == 10000
    assert body["paid_off"] is False


@pytest.mark.asyncio
async def test_a_payment_beyond_the_contract_puts_the_loan_ahead(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers, loan_first_payment_date=_first_payment(-1))
    checking = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "Checking", "type": "checking", "balance": 5000},
        )
    ).json()
    paid = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": checking["id"],
            "to_account_id": loan["id"],
            "amount": 3000,
            "date": app_today().isoformat(),
            "description": "Extra payment",
        },
    )
    assert paid.status_code == 201, paid.text

    body = (await client.get(f"/api/accounts/{loan['id']}/loan-schedule", headers=auth_headers)).json()

    # Nothing is due yet, so the contract still expects the full 10,000.
    assert body["current_debt"] == 7000
    assert body["scheduled_balance"] == 10000
    assert body["ahead_by"] == 3000
    assert body["projected_payoff_date"] < body["payoff_date"]


@pytest.mark.asyncio
async def test_an_incomplete_contract_lists_what_is_missing(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers, loan_term_months=None, loan_interest_rate=None)

    body = (await client.get(f"/api/accounts/{loan['id']}/loan-schedule", headers=auth_headers)).json()

    assert body["configured"] is False
    assert set(body["missing"]) == {"loan_term_months", "loan_interest_rate"}
    assert body["installments"] == []


@pytest.mark.asyncio
async def test_terms_are_only_kept_on_loans(client: AsyncClient, auth_headers):
    checking = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Checking", "type": "checking", "balance": 100, **TERMS},
    )
    loan = await _loan(client, auth_headers)
    retyped = await client.patch(
        f"/api/accounts/{loan['id']}", headers=auth_headers, json={"type": "checking"}
    )

    assert checking.status_code == 201, checking.text
    assert checking.json()["loan_kind"] is None
    assert retyped.status_code == 200, retyped.text
    assert retyped.json()["loan_term_months"] is None
    assert retyped.json()["loan_amortization"] is None
    schedule = await client.get(f"/api/accounts/{checking.json()['id']}/loan-schedule", headers=auth_headers)
    assert schedule.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("loan_interest_rate", 150),
        ("loan_term_months", 0),
        ("loan_term_months", 601),
        ("loan_original_amount", -1),
        ("loan_kind", "boat"),
        ("loan_rate_period", "weekly"),
        ("loan_amortization", "german"),
    ],
)
async def test_invalid_terms_are_rejected(client: AsyncClient, auth_headers, field: str, value):
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Loan", "type": "loan", "balance": 1000, **TERMS, field: value},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_terms_can_be_edited(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)

    updated = await client.patch(
        f"/api/accounts/{loan['id']}",
        headers=auth_headers,
        json={"loan_amortization": "sac", "loan_first_payment_date": date(2026, 3, 5).isoformat()},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["loan_amortization"] == "sac"
    assert updated.json()["loan_first_payment_date"] == "2026-03-05"
    assert updated.json()["loan_term_months"] == 12


@pytest.mark.asyncio
async def test_the_schedule_of_another_workspace_is_not_found(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
):
    other = Workspace(id=uuid.uuid4(), name="Other household", kind="personal", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()
    loan = await _loan(client, {**auth_headers, "X-Workspace-Id": str(other.id)})

    from_default = await client.get(f"/api/accounts/{loan['id']}/loan-schedule", headers=auth_headers)
    from_owner = await client.get(
        f"/api/accounts/{loan['id']}/loan-schedule",
        headers={**auth_headers, "X-Workspace-Id": str(other.id)},
    )

    assert from_default.status_code == 404
    assert from_owner.status_code == 200
