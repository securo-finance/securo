import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


async def _loan(client: AsyncClient, headers: dict, balance: float = 10000) -> dict:
    response = await client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Mortgage",
            "type": "loan",
            "balance": balance,
            "balance_date": (app_today() - timedelta(days=60)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _statement(client: AsyncClient, headers: dict, loan_id: str, **body):
    payload = {"balance": 10000, "date": app_today().isoformat(), "description": "Indexation and interest", **body}
    return await client.post(f"/api/accounts/{loan_id}/loan-balance", headers=headers, json=payload)


async def _transactions(client: AsyncClient, headers: dict, account_id: str) -> list[dict]:
    response = await client.get(
        "/api/transactions", headers=headers, params={"account_id": account_id, "limit": 100}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _month_expenses(client: AsyncClient, headers: dict, account_id: str) -> float:
    today = app_today()
    response = await client.get(
        f"/api/accounts/{account_id}/summary",
        headers=headers,
        params={"from": today.replace(day=1).isoformat(), "to": today.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()["monthly_expenses"]


@pytest.mark.asyncio
async def test_a_higher_statement_records_the_growth_as_a_loan_cost(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)
    category = (
        await client.post("/api/categories", headers=auth_headers, json={"name": "Loan interest", "icon": "percent", "color": "#ef4444"})
    ).json()

    response = await _statement(client, auth_headers, loan["id"], balance=10250.40, category_id=category["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adjustment"] == 250.40
    assert body["current_debt"] == 10250.40
    adjustment = next(tx for tx in await _transactions(client, auth_headers, loan["id"]) if tx["id"] == body["transaction_id"])
    assert adjustment["type"] == "debit"
    assert float(adjustment["amount"]) == 250.40
    assert adjustment["source"] == "manual"
    assert adjustment["category_id"] == category["id"]
    assert adjustment["exclude_from_pnl"] is False
    assert await _month_expenses(client, auth_headers, loan["id"]) == 250.40


@pytest.mark.asyncio
async def test_a_lower_statement_moves_the_balance_but_is_not_income(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)

    body = (await _statement(client, auth_headers, loan["id"], balance=9800)).json()

    assert body["adjustment"] == -200
    assert body["current_debt"] == 9800
    adjustment = next(tx for tx in await _transactions(client, auth_headers, loan["id"]) if tx["id"] == body["transaction_id"])
    assert adjustment["type"] == "credit"
    assert adjustment["exclude_from_pnl"] is True


@pytest.mark.asyncio
async def test_a_matching_statement_records_nothing(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)
    before = await _transactions(client, auth_headers, loan["id"])

    body = (await _statement(client, auth_headers, loan["id"], balance=10000)).json()

    assert body == {"adjustment": 0, "current_debt": 10000, "transaction_id": None}
    assert len(await _transactions(client, auth_headers, loan["id"])) == len(before)


@pytest.mark.asyncio
async def test_the_statement_is_compared_with_the_ledger_on_its_own_date(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)
    checking = (
        await client.post("/api/accounts", headers=auth_headers, json={"name": "Checking", "type": "checking", "balance": 5000})
    ).json()
    paid = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": checking["id"],
            "to_account_id": loan["id"],
            "amount": 1000,
            "date": app_today().isoformat(),
            "description": "Installment",
        },
    )
    assert paid.status_code == 201, paid.text

    # Last week's statement predates the payment: only the 100 of growth is new.
    body = (
        await _statement(
            client, auth_headers, loan["id"], balance=10100, date=(app_today() - timedelta(days=7)).isoformat()
        )
    ).json()

    assert body["adjustment"] == 100
    assert body["current_debt"] == 9100


@pytest.mark.asyncio
async def test_invalid_statements_are_rejected(client: AsyncClient, auth_headers):
    loan = await _loan(client, auth_headers)
    checking = (
        await client.post("/api/accounts", headers=auth_headers, json={"name": "Checking", "type": "checking", "balance": 5000})
    ).json()

    future = await _statement(client, auth_headers, loan["id"], date=(app_today() + timedelta(days=1)).isoformat())
    negative = await _statement(client, auth_headers, loan["id"], balance=-1)
    not_a_loan = await _statement(client, auth_headers, checking["id"])
    unknown_category = await _statement(client, auth_headers, loan["id"], balance=10500, category_id=str(uuid.uuid4()))

    assert future.status_code == 400
    assert negative.status_code == 422
    assert not_a_loan.status_code == 404
    assert unknown_category.status_code == 400
    assert await _transactions(client, auth_headers, loan["id"]) == []


@pytest.mark.asyncio
async def test_a_loan_of_another_workspace_cannot_be_adjusted(
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

    response = await _statement(client, auth_headers, loan["id"], balance=10500)

    assert response.status_code == 404
