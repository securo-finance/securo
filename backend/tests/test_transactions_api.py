import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.user import User


@pytest.mark.asyncio
async def test_list_transactions(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5
    assert data["page"] == 1
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_list_transactions_pagination(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    response = await client.get(
        "/api/transactions?page=1&limit=2", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_list_transactions_filter_by_account(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction], test_account: Account
):
    response = await client.get(
        f"/api/transactions?account_id={test_account.id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5  # all belong to same account


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    cat_id = test_categories[0].id  # Alimentação
    response = await client.get(
        f"/api/transactions?category_id={cat_id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1  # only IFOOD


@pytest.mark.asyncio
async def test_list_transactions_filter_by_date(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    # Use actual fixture transaction dates (UBER and IFOOD)
    uber_date = test_transactions[0].date.isoformat()
    ifood_date = test_transactions[1].date.isoformat()
    date_from = min(uber_date, ifood_date)
    date_to = max(uber_date, ifood_date)
    response = await client.get(
        f"/api/transactions?from={date_from}&to={date_to}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2  # at least UBER and IFOOD


@pytest.mark.asyncio
async def test_get_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn_id = str(test_transactions[0].id)
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "UBER TRIP"
    assert data["source"] == "manual"


@pytest.mark.asyncio
async def test_get_transaction_not_found(client: AsyncClient, auth_headers, test_transactions):
    response = await client.get(
        "/api/transactions/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_transaction(
    client: AsyncClient, auth_headers, test_account: Account, test_categories: list[Category]
):
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "category_id": str(test_categories[0].id),
            "description": "Almoço restaurante",
            "amount": "32.50",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Almoço restaurante"
    assert data["source"] == "manual"
    assert data["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_create_transaction_auto_categorize(
    client: AsyncClient, auth_headers, test_account: Account,
    test_rules, test_categories: list[Category],
):
    """Transaction with UBER in description should auto-categorize to Transporte."""
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "UBER TRIP CENTRO",
            "amount": "18.00",
            "date": "2026-02-21",
            "type": "debit",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["category_id"] == str(test_categories[1].id)  # Transporte


@pytest.mark.asyncio
async def test_create_transaction_invalid_account(
    client: AsyncClient, auth_headers, test_account
):
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": "00000000-0000-0000-0000-000000000000",
            "description": "Test",
            "amount": "10.00",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_transaction_requires_current_month(
    client: AsyncClient, auth_headers, test_account: Account, test_categories: list[Category], session
):
    await session.execute(
        update(User)
        .values(
            preferences={
                "language": "pt-BR",
                "date_format": "DD/MM/YYYY",
                "timezone": "America/Sao_Paulo",
                "currency_display": "BRL",
            }
        )
    )
    await session.commit()

    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "category_id": str(test_categories[0].id),
            "description": "Almoço restaurante",
            "amount": "32.50",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 400
    assert "Mês Atual não definido" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    txn_id = str(test_transactions[4].id)  # NETFLIX, no category
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"category_id": str(test_categories[0].id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_update_transaction_remove_category(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Setting category_id to null must clear an existing category."""
    txn_id = str(test_transactions[1].id)  # IFOOD — has category (Alimentação)
    assert test_transactions[1].category_id is not None

    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"category_id": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category_id"] is None

    # Verify the change persisted
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["category_id"] is None


@pytest.mark.asyncio
async def test_update_transaction_date(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """Regression: updating the date field must not fail with 'input should be none'."""
    txn_id = str(test_transactions[0].id)
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"date": "2026-06-15"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-06-15"


@pytest.mark.asyncio
async def test_update_transaction_all_fields(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Regression: updating multiple fields including date must succeed."""
    txn_id = str(test_transactions[0].id)
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={
            "description": "Updated description",
            "amount": "999.99",
            "date": "2026-12-25",
            "type": "credit",
            "currency": "USD",
            "category_id": str(test_categories[0].id),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "Updated description"
    assert float(data["amount"]) == 999.99
    assert data["date"] == "2026-12-25"
    assert data["type"] == "credit"
    assert data["currency"] == "USD"
    assert data["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_delete_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn_id = str(test_transactions[4].id)  # NETFLIX
    response = await client.delete(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_transaction_not_found(client: AsyncClient, auth_headers, test_transactions):
    response = await client.delete(
        "/api/transactions/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_transactions_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/transactions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_transaction_without_account_fails(
    client: AsyncClient, auth_headers, test_account
):
    """account_id is required — omitting it must return 422."""
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "No account",
            "amount": "10.00",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 422
