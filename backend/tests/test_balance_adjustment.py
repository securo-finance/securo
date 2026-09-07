from datetime import date, timedelta
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction


async def _create_manual_account(
    client: AsyncClient,
    auth_headers: dict,
    *,
    account_type: str = "checking",
    balance: str = "300.00",
) -> str:
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Balance adjustment test",
            "type": account_type,
            "balance": balance,
            "currency": "BRL",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_adjust_manual_account_to_absolute_balance(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
):
    account_id = await _create_manual_account(client, auth_headers)

    response = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "230.00", "exclude_from_pnl": True},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["previous_balance"] == 300.0
    assert payload["target_balance"] == 230.0
    assert payload["adjustment_amount"] == -70.0
    assert payload["transaction"]["type"] == "debit"
    assert float(payload["transaction"]["amount"]) == 70.0
    assert payload["transaction"]["date"] == date.today().isoformat()
    assert payload["transaction"]["source"] == "balance_adjustment"
    assert payload["transaction"]["exclude_from_pnl"] is True

    account = await client.get(f"/api/accounts/{account_id}", headers=auth_headers)
    summary = await client.get(f"/api/accounts/{account_id}/summary", headers=auth_headers)
    assert account.json()["current_balance"] == 230.0
    assert summary.json()["current_balance"] == 230.0
    assert summary.json()["monthly_expenses"] == 0.0

    cash_flow = await client.get(
        "/api/reports/cash-flow",
        headers=auth_headers,
        params={"months": 1, "interval": "daily", "account_ids": account_id},
    )
    assert cash_flow.status_code == 200, cash_flow.text
    cash_flow_data = cash_flow.json()
    cash_flow_breakdowns = {
        row["key"]: row["value"] for row in cash_flow_data["summary"]["breakdowns"]
    }
    assert cash_flow_breakdowns["startingBalance"] == 230.0
    today_flow = next(
        row for row in cash_flow_data["trend"] if row["date"] == date.today().isoformat()
    )
    assert today_flow["breakdowns"] == {"inflow": 0.0, "outflow": 0.0}

    net_worth = await client.get(
        "/api/reports/net-worth",
        headers=auth_headers,
        params={"months": 1, "interval": "daily", "account_ids": account_id},
    )
    assert net_worth.status_code == 200, net_worth.text
    net_worth_breakdowns = {
        row["key"]: row["value"] for row in net_worth.json()["summary"]["breakdowns"]
    }
    assert net_worth_breakdowns["accounts"] == 230.0

    transaction = await session.scalar(
        select(Transaction).where(
            Transaction.account_id == uuid.UUID(account_id),
            Transaction.source == "balance_adjustment",
        )
    )
    assert transaction is not None
    assert transaction.description == "Manual balance adjustment"


@pytest.mark.asyncio
async def test_adjust_credit_card_accepts_positive_amount_owed(
    client: AsyncClient,
    auth_headers,
):
    account_id = await _create_manual_account(
        client,
        auth_headers,
        account_type="credit_card",
        balance="500.00",
    )

    response = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "320.00", "exclude_from_pnl": True},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["previous_balance"] == 500.0
    assert payload["target_balance"] == 320.0
    assert payload["adjustment_amount"] == 180.0
    assert payload["transaction"]["type"] == "credit"
    assert float(payload["transaction"]["amount"]) == 180.0

    summary = await client.get(f"/api/accounts/{account_id}/summary", headers=auth_headers)
    assert summary.json()["current_balance"] == -320.0


@pytest.mark.asyncio
async def test_adjustment_can_be_included_in_reports(client: AsyncClient, auth_headers):
    account_id = await _create_manual_account(client, auth_headers)

    response = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "230.00", "exclude_from_pnl": False},
    )
    assert response.status_code == 201, response.text
    transaction_id = response.json()["transaction"]["id"]

    summary = await client.get(f"/api/accounts/{account_id}/summary", headers=auth_headers)
    assert summary.json()["monthly_expenses"] == 70.0

    hidden = await client.patch(
        f"/api/transactions/{transaction_id}",
        headers=auth_headers,
        json={"exclude_from_pnl": True, "notes": "Verified against bank balance"},
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["notes"] == "Verified against bank balance"
    hidden_summary = await client.get(
        f"/api/accounts/{account_id}/summary", headers=auth_headers
    )
    assert hidden_summary.json()["monthly_expenses"] == 0.0
    assert hidden_summary.json()["current_balance"] == 230.0

    locked_updates = [
        {"description": "Not an adjustment"},
        {"date": (date.today() - timedelta(days=1)).isoformat()},
        {"currency": "USD"},
        {"account_id": str(uuid.uuid4())},
        {"category_id": str(uuid.uuid4())},
        {"payee_id": str(uuid.uuid4())},
        {"amount_primary": "100.00"},
        {"fx_rate_used": "1.25"},
        {"is_ignored": True},
        {"status": "pending"},
        {"effective_bill_date": date.today().isoformat()},
        {"splits": {"share_type": "equal", "splits": []}},
    ]
    for update in locked_updates:
        locked = await client.patch(
            f"/api/transactions/{transaction_id}",
            headers=auth_headers,
            json=update,
        )
        assert locked.status_code == 400, update


@pytest.mark.asyncio
async def test_connected_account_layers_adjustment_on_provider_balance(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
):
    response = await client.post(
        f"/api/accounts/{test_account.id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "1200.00"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["adjustment_amount"] == -300.0
    transaction_id = response.json()["transaction"]["id"]

    account = await client.get(f"/api/accounts/{test_account.id}", headers=auth_headers)
    listed = await client.get("/api/accounts", headers=auth_headers)
    listed_account = next(row for row in listed.json() if row["id"] == str(test_account.id))
    assert account.json()["current_balance"] == 1200.0
    assert listed_account["current_balance"] == 1200.0

    edited = await client.patch(
        f"/api/transactions/{transaction_id}",
        headers=auth_headers,
        json={"amount": "100.00"},
    )
    assert edited.status_code == 200, edited.text
    after_edit = await client.get(
        f"/api/accounts/{test_account.id}", headers=auth_headers
    )
    assert after_edit.json()["current_balance"] == 1400.0

    changed_type = await client.patch(
        f"/api/transactions/{transaction_id}",
        headers=auth_headers,
        json={"type": "credit"},
    )
    assert changed_type.status_code == 200, changed_type.text
    after_type_change = await client.get(
        f"/api/accounts/{test_account.id}", headers=auth_headers
    )
    assert after_type_change.json()["current_balance"] == 1600.0

    deleted = await client.delete(
        f"/api/transactions/{transaction_id}", headers=auth_headers
    )
    assert deleted.status_code == 204, deleted.text
    after_delete = await client.get(
        f"/api/accounts/{test_account.id}", headers=auth_headers
    )
    assert after_delete.json()["current_balance"] == 1500.0


@pytest.mark.asyncio
async def test_adjustment_rejects_negative_card_target_and_noop(
    client: AsyncClient,
    auth_headers,
):
    account_id = await _create_manual_account(
        client,
        auth_headers,
        account_type="credit_card",
        balance="500.00",
    )

    negative = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "-1.00"},
    )
    noop = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=auth_headers,
        json={"balance": "500.00"},
    )

    assert negative.status_code == 400
    assert noop.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_adjust_balance(
    client: AsyncClient,
    auth_headers,
    viewer_auth_headers,
):
    account_id = await _create_manual_account(client, auth_headers)
    response = await client.post(
        f"/api/accounts/{account_id}/adjust-balance",
        headers=viewer_auth_headers,
        json={"balance": "200.00"},
    )
    assert response.status_code == 403
