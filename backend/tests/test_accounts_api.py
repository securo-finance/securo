import pytest
from httpx import AsyncClient

from app.models.account import Account


@pytest.mark.asyncio
async def test_list_accounts(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.get("/api/accounts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Conta Corrente"
    assert data[0]["type"] == "checking"
    assert data[0]["currency"] == "BRL"


@pytest.mark.asyncio
async def test_list_accounts_empty(client: AsyncClient, auth_headers):
    response = await client.get("/api/accounts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


@pytest.mark.asyncio
async def test_get_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.get(
        f"/api/accounts/{test_account.id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Conta Corrente"
    assert data["balance"] == "1500.00"


@pytest.mark.asyncio
async def test_get_account_not_found(client: AsyncClient, auth_headers, test_account):
    response = await client.get(
        "/api/accounts/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_manual_account(client: AsyncClient, auth_headers):
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Carteira",
            "type": "checking",
            "balance": "500.00",
            "currency": "BRL",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Carteira"
    assert data["balance"] == "500.00"
    assert data["connection_id"] is None
    assert data["external_id"] is None


@pytest.mark.asyncio
async def test_update_manual_account(client: AsyncClient, auth_headers):
    # Create a manual account first
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Poupança", "type": "savings", "balance": "1000.00"},
    )
    account_id = create_resp.json()["id"]

    # Update it
    response = await client.patch(
        f"/api/accounts/{account_id}",
        headers=auth_headers,
        json={"name": "Poupança Atualizada", "balance": "2000.00"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Poupança Atualizada"
    assert data["balance"] == "2000.00"


@pytest.mark.asyncio
async def test_update_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"name": "Hacked"},
    )
    assert response.status_code == 400
    assert "bank-connected" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_manual_account(client: AsyncClient, auth_headers):
    # Create a manual account
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Temp", "type": "checking"},
    )
    account_id = create_resp.json()["id"]

    # Delete it
    response = await client.delete(f"/api/accounts/{account_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone
    response = await client.get(f"/api/accounts/{account_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.delete(
        f"/api/accounts/{test_account.id}", headers=auth_headers
    )
    assert response.status_code == 400
    assert "bank-connected" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_accounts_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/accounts")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_account_with_balance_creates_opening_transaction(
    client: AsyncClient, auth_headers, session
):
    """Creating an account with balance > 0 must create an opening_balance transaction in the DB."""
    from sqlalchemy import select
    from app.models.transaction import Transaction as TxModel
    import uuid as _uuid
    from decimal import Decimal

    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Carteira", "type": "wallet", "balance": "500.00", "currency": "BRL"},
    )
    assert response.status_code == 201
    account_id = response.json()["id"]

    # Verify the opening_balance transaction was actually written to the DB
    result = await session.execute(
        select(TxModel).where(
            TxModel.account_id == _uuid.UUID(account_id),
            TxModel.source == "opening_balance",
        )
    )
    opening_tx = result.scalar_one_or_none()
    assert opening_tx is not None, "opening_balance transaction should exist in DB"
    assert opening_tx.amount == Decimal("500.00")
    assert opening_tx.type == "credit"
    assert opening_tx.description == "Saldo inicial"

    # Also verify it does NOT appear in the public transaction list
    txn_response = await client.get(
        f"/api/transactions?account_id={account_id}", headers=auth_headers
    )
    assert txn_response.status_code == 200
    items = txn_response.json()["items"]
    assert all(t["source"] != "opening_balance" for t in items)


@pytest.mark.asyncio
async def test_create_account_zero_balance_no_opening_transaction(
    client: AsyncClient, auth_headers
):
    """Creating an account with balance = 0 must NOT create any transaction."""
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Vazia", "type": "checking", "balance": "0", "currency": "BRL"},
    )
    assert response.status_code == 201
    account_id = response.json()["id"]

    txn_response = await client.get(
        f"/api/transactions?account_id={account_id}", headers=auth_headers
    )
    assert txn_response.json()["total"] == 0


@pytest.mark.asyncio
async def test_account_summary(
    client: AsyncClient, auth_headers, test_account, test_transactions
):
    """Summary endpoint returns computed balance from transactions (current month).

    Fixture transactions (current month, no opening_balance):
      credit: SALARIO FEV 8000.00, PIX RECEBIDO 150.00  → income = 8150.00
      debit:  UBER TRIP 25.50, IFOOD 45.00, NETFLIX 39.90 → expenses = 110.40

    Note: test_account is bank-connected so current_balance uses the stored
    Account.balance (1500.00), not the computed sum from transactions.
    """
    expected_income = 8000.00 + 150.00          # 8150.00
    expected_expenses = 25.50 + 45.00 + 39.90   # 110.40

    account_id = str(test_account.id)
    response = await client.get(f"/api/accounts/{account_id}/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["monthly_income"] == pytest.approx(expected_income)
    assert data["monthly_expenses"] == pytest.approx(expected_expenses)
    # Bank-connected accounts use stored balance from provider, not computed
    assert data["current_balance"] == pytest.approx(1500.00)
    assert isinstance(data["account_id"], str)


@pytest.mark.asyncio
async def test_account_summary_not_found(client: AsyncClient, auth_headers):
    response = await client.get(
        "/api/accounts/00000000-0000-0000-0000-000000000000/summary",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_accounts_includes_manual_and_bank_connected(
    client: AsyncClient, auth_headers, test_account: Account
):
    """Accounts list returns both manual and bank-connected accounts for the sidebar."""
    # test_account is bank-connected (has connection_id). Create a manual account too.
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Carteira Manual", "type": "savings", "balance": "200.00", "currency": "BRL"},
    )
    assert create_resp.status_code == 201

    response = await client.get("/api/accounts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 2, f"Expected 2 accounts (1 bank + 1 manual), got {len(data)}"

    names = {a["name"] for a in data}
    assert "Conta Corrente" in names, "Bank-connected account should appear"
    assert "Carteira Manual" in names, "Manual account should appear"

    # Verify we can distinguish them by connection_id
    bank_acct = next(a for a in data if a["name"] == "Conta Corrente")
    manual_acct = next(a for a in data if a["name"] == "Carteira Manual")
    assert bank_acct["connection_id"] is not None, "Bank account should have connection_id"
    assert manual_acct["connection_id"] is None, "Manual account should have no connection_id"


@pytest.mark.asyncio
async def test_credit_card_account_returns_negative_current_balance(
    client: AsyncClient, auth_headers
):
    """Credit card accounts represent debt (liabilities).

    The stored balance remains positive (raw from bank), but the API returns
    current_balance negated so consumers see it as a liability.
    """
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Nubank Roxinho",
            "type": "credit_card",
            "balance": "3500.00",
            "currency": "BRL",
        },
    )
    assert create_resp.status_code == 201
    account_id = create_resp.json()["id"]

    # Fetch the individual account — raw balance unchanged
    response = await client.get(f"/api/accounts/{account_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["type"] == "credit_card"
    assert data["balance"] == "3500.00", "Raw stored balance should remain positive"
    assert data["connection_id"] is None

    # Verify current_balance is negated in the list endpoint
    list_resp = await client.get("/api/accounts", headers=auth_headers)
    cc_accounts = [a for a in list_resp.json() if a["type"] == "credit_card"]
    assert len(cc_accounts) >= 1
    assert cc_accounts[0]["balance"] == "3500.00", "Raw balance stays positive"
    assert cc_accounts[0]["current_balance"] == pytest.approx(-3500.00), (
        "current_balance should be negated for credit cards"
    )


@pytest.mark.asyncio
async def test_dashboard_total_balance_subtracts_credit_card_debt(
    client: AsyncClient, auth_headers
):
    """Dashboard total_balance subtracts credit card debt as a liability.

    Credit card balances are negated in the backend: a credit card with a 2000
    opening balance contributes -2000 to total_balance. A checking account with
    5000 contributes +5000. Net total = 5000 - 2000 = 3000.
    """
    # Create a checking account with 5000 balance
    checking_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Checking", "type": "checking", "balance": "5000.00", "currency": "BRL"},
    )
    assert checking_resp.status_code == 201

    # Create a credit card account with 2000 balance (represents debt)
    cc_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Credit Card", "type": "credit_card", "balance": "2000.00", "currency": "BRL"},
    )
    assert cc_resp.status_code == 201

    resp = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    total = sum(float(v) for v in data["total_balance"].values())
    assert total == 3000.0, (
        f"Expected total_balance=3000.0 (5000 checking - 2000 CC debt); got {total}"
    )


@pytest.mark.asyncio
async def test_credit_card_summary_returns_negative_balance(
    client: AsyncClient, auth_headers
):
    """Account summary for a credit card should return negative current_balance."""
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "CC Summary Test",
            "type": "credit_card",
            "balance": "1200.00",
            "currency": "BRL",
        },
    )
    assert create_resp.status_code == 201
    account_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/accounts/{account_id}/summary", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()

    assert data["current_balance"] == pytest.approx(-1200.00), (
        f"Credit card summary current_balance should be -1200; got {data['current_balance']}"
    )


@pytest.mark.asyncio
async def test_list_accounts_includes_previous_balance(client: AsyncClient, auth_headers):
    """Account list should include previous_balance field."""
    # Create account
    response = await client.post(
        "/api/accounts",
        json={"name": "Test PB", "type": "checking", "balance": 1000},
        headers=auth_headers,
    )
    assert response.status_code == 201

    # List accounts
    response = await client.get("/api/accounts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    # Find our account
    test_acc = [a for a in data if a["name"] == "Test PB"][0]
    assert "previous_balance" in test_acc
    assert isinstance(test_acc["previous_balance"], (int, float))


@pytest.mark.asyncio
async def test_list_accounts_include_closed(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "ClosedTest", "type": "checking", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    await client.post(f"/api/accounts/{acct_id}/close", headers=auth_headers)

    resp_default = await client.get("/api/accounts", headers=auth_headers)
    ids_default = [a["id"] for a in resp_default.json()]
    assert acct_id not in ids_default

    resp_all = await client.get("/api/accounts?include_closed=true", headers=auth_headers)
    ids_all = [a["id"] for a in resp_all.json()]
    assert acct_id in ids_all


@pytest.mark.asyncio
async def test_get_account_balance_history(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "BH Acct", "type": "checking", "balance": 1000, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    resp = await client.get(f"/api/accounts/{acct_id}/balance-history", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_account_balance_history_with_dates(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "BH Dates", "type": "checking", "balance": 500, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    from datetime import date
    today = date.today().isoformat()
    resp = await client.get(
        f"/api/accounts/{acct_id}/balance-history",
        params={"from": today, "to": today},
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_account_balance_history_not_found(client: AsyncClient, auth_headers):
    import uuid
    resp = await client.get(f"/api/accounts/{uuid.uuid4()}/balance-history", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_close_account(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "Close Me", "type": "checking", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    resp = await client.post(f"/api/accounts/{acct_id}/close", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_closed"] is True


@pytest.mark.asyncio
async def test_reopen_account(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "Reopen Me", "type": "checking", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    await client.post(f"/api/accounts/{acct_id}/close", headers=auth_headers)
    resp = await client.post(f"/api/accounts/{acct_id}/reopen", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_closed"] is False


@pytest.mark.asyncio
async def test_close_account_not_found(client: AsyncClient, auth_headers):
    import uuid
    resp = await client.post(f"/api/accounts/{uuid.uuid4()}/close", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reopen_account_not_found(client: AsyncClient, auth_headers):
    import uuid
    resp = await client.post(f"/api/accounts/{uuid.uuid4()}/reopen", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_account_summary_with_dates(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts",
        json={"name": "Sum Dates", "type": "checking", "balance": 1000, "currency": "BRL"},
        headers=auth_headers,
    )
    acct_id = resp.json()["id"]
    from datetime import date
    today = date.today().isoformat()
    resp = await client.get(
        f"/api/accounts/{acct_id}/summary",
        params={"from": today, "to": today},
        headers=auth_headers,
    )
    assert resp.status_code == 200


# --- display_name tests ---


@pytest.mark.asyncio
async def test_display_name_returned_in_list(client: AsyncClient, auth_headers):
    """display_name field must be present in the accounts list response."""
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Conta Teste", "type": "checking", "balance": "0"},
    )
    assert create_resp.status_code == 201
    assert "display_name" in create_resp.json()


@pytest.mark.asyncio
async def test_set_display_name_on_manual_account(client: AsyncClient, auth_headers):
    """Setting display_name on a manual account must be persisted and returned."""
    create_resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Nubank", "type": "checking", "balance": "0"},
    )
    account_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/accounts/{account_id}",
        headers=auth_headers,
        json={"display_name": "Nu Pessoal"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Nu Pessoal"


@pytest.mark.asyncio
async def test_set_display_name_on_connected_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    """display_name must be editable on bank-connected accounts."""
    response = await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"display_name": "Minha Conta"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Minha Conta"


@pytest.mark.asyncio
async def test_clear_display_name(client: AsyncClient, auth_headers, test_account: Account):
    """Setting display_name to null must clear it."""
    await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"display_name": "Apelido"},
    )

    clear_resp = await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"display_name": None},
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["display_name"] is None


@pytest.mark.asyncio
async def test_connected_account_name_still_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    """Editing 'name' on a bank-connected account must still be rejected even when display_name is allowed."""
    response = await client.patch(
        f"/api/accounts/{test_account.id}",
        headers=auth_headers,
        json={"name": "Nome Hackeado"},
    )
    assert response.status_code == 400
    assert "bank-connected" in response.json()["detail"].lower()
