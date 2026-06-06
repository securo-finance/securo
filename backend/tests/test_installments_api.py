import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction


@pytest.fixture
async def installment_transactions(
    session, test_user, test_account, test_categories
):
    """Create installment transactions for testing."""
    purchase_date = date(2024, 1, 15)
    txs = []
    for i in range(1, 4):  # 3-month installment
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=test_account.id,
            category_id=test_categories[0].id,
            description=f"Compra TV 12x - {i}/12",
            amount=Decimal("500.00"),
            date=purchase_date + timedelta(days=30 * (i - 1)),
            effective_date=purchase_date + timedelta(days=30 * (i - 1)),
            type="debit",
            source="pluggy",
            status="posted",
            installment_number=i,
            total_installments=12,
            installment_total_amount=Decimal("6000.00"),
            installment_purchase_date=purchase_date,
        )
        session.add(tx)
        txs.append(tx)
    await session.commit()
    return txs


@pytest.fixture
async def manual_installment(session, test_user, test_account, test_categories):
    """Create a manual installment transaction."""
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        category_id=test_categories[0].id,
        description="Sofá 3x",
        amount=Decimal("333.33"),
        date=date(2024, 1, 15),
        effective_date=date(2024, 1, 15),
        type="debit",
        source="manual",
        status="posted",
        installment_number=1,
        total_installments=3,
        installment_total_amount=Decimal("999.99"),
        installment_purchase_date=date(2024, 1, 15),
        notes=json.dumps({"manual_installment": True, "monthly_amount": "333.33"}),
    )
    session.add(tx)
    await session.commit()
    return tx


@pytest.mark.asyncio
async def test_get_summary_empty(client, auth_headers):
    response = await client.get("/api/installments/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["active_purchases_count"] == 0
    assert data["total_estimated_amount"] == 0.0
    assert data["total_paid_amount"] == 0.0


@pytest.mark.asyncio
async def test_get_summary_with_installments(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["active_purchases_count"] == 1
    assert data["total_estimated_amount"] == 6000.0
    assert data["total_paid_amount"] == 1500.0
    assert data["total_remaining_amount"] == 4500.0
    assert data["overall_progress_percentage"] == 25.0


@pytest.mark.asyncio
async def test_list_purchases_empty(client, auth_headers):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_purchases(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    purchase = data[0]
    assert purchase["merchant_name"] == "Compra TV 12x - 1/12"
    assert purchase["total_installments"] == 12
    assert purchase["paid_count"] == 3
    assert purchase["status"] == "ACTIVE"
    assert purchase["progress_percentage"] == 25.0


@pytest.mark.asyncio
async def test_list_purchases_filter_active(
    client, auth_headers, installment_transactions
):
    response = await client.get(
        "/api/installments/purchases", headers=auth_headers, params={"status": "ACTIVE"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


@pytest.mark.asyncio
async def test_list_purchases_filter_finished(
    client, auth_headers, installment_transactions
):
    response = await client.get(
        "/api/installments/purchases", headers=auth_headers, params={"status": "FINISHED"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_purchases_sort_by_amount(
    client, auth_headers, installment_transactions
):
    response = await client.get(
        "/api/installments/purchases", headers=auth_headers, params={"sort": "amount"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["total_amount"] == 6000.0


@pytest.mark.asyncio
async def test_get_purchase_details(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.get(
        f"/api/installments/purchases/{purchase_id}/details", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_amount"] == 6000.0
    assert data["total_installments"] == 12
    assert data["account_id"] == str(installment_transactions[0].account_id)
    assert len(data["installments_timeline"]) == 12


@pytest.mark.asyncio
async def test_get_purchase_details_not_found(client, auth_headers):
    fake_id = str(uuid.uuid4())
    response = await client.get(
        f"/api/installments/purchases/{fake_id}/details", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_manual_installment(client, auth_headers, test_account):
    response = await client.post(
        "/api/installments/purchases",
        headers=auth_headers,
        json={
            "merchant_name": "Notebook Dell",
            "account_id": str(test_account.id),
            "total_amount": "3000.00",
            "total_installments": 6,
            "purchase_date": "2024-01-15",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["merchant_name"] == "Notebook Dell"
    assert data["total_installments"] == 6
    assert data["is_manual"] is True
    assert data["installment_monthly_amount"] == 500.0


@pytest.mark.asyncio
async def test_create_manual_installment_with_category(
    client, auth_headers, test_account, test_categories
):
    response = await client.post(
        "/api/installments/purchases",
        headers=auth_headers,
        json={
            "merchant_name": "iPhone",
            "account_id": str(test_account.id),
            "total_amount": "6000.00",
            "total_installments": 12,
            "purchase_date": "2024-01-15",
            "category_id": str(test_categories[0].id),
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["category"] is not None
    assert data["category"]["name"] == test_categories[0].name


@pytest.mark.asyncio
async def test_create_manual_installment_invalid_account(client, auth_headers):
    response = await client.post(
        "/api/installments/purchases",
        headers=auth_headers,
        json={
            "merchant_name": "Test",
            "account_id": str(uuid.uuid4()),
            "total_amount": "1000.00",
            "total_installments": 3,
            "purchase_date": "2024-01-15",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_manual_installment(
    client, auth_headers, manual_installment, test_account
):
    # Get purchase ID
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    # Update
    response = await client.patch(
        f"/api/installments/purchases/{purchase_id}",
        headers=auth_headers,
        json={
            "merchant_name": "Sofá Atualizado",
            "monthly_amount": "400.00",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_name"] == "Sofá Atualizado"
    assert data["installment_monthly_amount"] == 400.0


@pytest.mark.asyncio
async def test_update_manual_installment_not_manual(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.patch(
        f"/api/installments/purchases/{purchase_id}",
        headers=auth_headers,
        json={"merchant_name": "Hacked"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_manual_installment(
    client, auth_headers, manual_installment
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.delete(
        f"/api/installments/purchases/{purchase_id}", headers=auth_headers
    )
    assert response.status_code == 204

    # Verify deleted
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_delete_manual_installment_not_manual(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.delete(
        f"/api/installments/purchases/{purchase_id}", headers=auth_headers
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_mark_installment_paid(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.post(
        f"/api/installments/purchases/{purchase_id}/pay",
        headers=auth_headers,
        json={
            "installment_number": 4,
            "amount": "500.00",
            "date": "2024-04-15",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["paid_count"] == 4
    assert data["progress_percentage"] == pytest.approx(33.3, rel=0.1)


@pytest.mark.asyncio
async def test_mark_installment_paid_already_paid(
    client, auth_headers, installment_transactions
):
    response = await client.get("/api/installments/purchases", headers=auth_headers)
    purchase_id = response.json()[0]["id"]

    response = await client.post(
        f"/api/installments/purchases/{purchase_id}/pay",
        headers=auth_headers,
        json={
            "installment_number": 1,  # Already paid
            "amount": "500.00",
            "date": "2024-04-15",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_purchases_filter_by_account(
    client, auth_headers, installment_transactions, test_account
):
    response = await client.get(
        "/api/installments/purchases",
        headers=auth_headers,
        params={"account_id": str(test_account.id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


@pytest.mark.asyncio
async def test_list_purchases_filter_by_other_account(
    client, auth_headers, installment_transactions
):
    response = await client.get(
        "/api/installments/purchases",
        headers=auth_headers,
        params={"account_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0
