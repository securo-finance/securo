import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.asyncio
async def test_create_and_list_debt(client: AsyncClient, auth_headers: dict, test_user: User):
    response = await client.post(
        "/api/debts",
        json={
            "kind": "loan",
            "creditor_name": "Banco Teste",
            "original_principal": "1000.00",
            "current_balance": "1000.00",
            "currency": "BRL",
            "opened_date": "2026-01-01",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    debt = response.json()
    assert debt["creditor_name"] == "Banco Teste"
    assert debt["status"] == "active"
    assert debt["plans"] == []

    listing = await client.get("/api/debts", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_create_plan_and_pay_installment_over_http(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    create = await client.post(
        "/api/debts",
        json={
            "kind": "payroll_loan",
            "creditor_name": "Banco Consignado",
            "original_principal": "500.00",
            "current_balance": "500.00",
            "currency": "BRL",
            "opened_date": "2026-01-01",
        },
        headers=auth_headers,
    )
    debt_id = create.json()["id"]

    plan_resp = await client.post(
        f"/api/debts/{debt_id}/plans",
        json={
            "kind": "original_contract",
            "collection_mode": "payroll_deduction",
            "interest_rate": "0",
            "installment_amount": "250.00",
            "num_installments": 2,
            "first_due_date": "2026-02-01",
            "frequency": "monthly",
            "activate": True,
        },
        headers=auth_headers,
    )
    assert plan_resp.status_code == 201
    plan = plan_resp.json()
    assert plan["status"] == "active"
    assert len(plan["installments"]) == 2

    installment_id = plan["installments"][0]["id"]
    pay_resp = await client.post(
        f"/api/debts/installments/{installment_id}/pay",
        json={"paid_date": "2026-02-01"},
        headers=auth_headers,
    )
    assert pay_resp.status_code == 200
    assert pay_resp.json()["status"] == "paid"
    assert pay_resp.json()["transaction_id"] is None

    # Rejecting a transaction link on a payroll-deducted installment.
    second_installment_id = plan["installments"][1]["id"]
    rejected = await client.post(
        f"/api/debts/installments/{second_installment_id}/pay",
        json={"paid_date": "2026-03-01", "transaction_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_headers,
    )
    assert rejected.status_code == 400

    detail = await client.get(f"/api/debts/{debt_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["current_balance"] == "250.00"


@pytest.mark.asyncio
async def test_payoff_projection_and_strategy_setting_over_http(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    setting = await client.get("/api/debts/strategy-setting", headers=auth_headers)
    assert setting.status_code == 200
    assert setting.json()["method"] == "avalanche"

    updated = await client.patch(
        "/api/debts/strategy-setting",
        json={"method": "snowball", "extra_monthly_amount": "50.00"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["method"] == "snowball"

    projection = await client.get("/api/debts/payoff-projection", headers=auth_headers)
    assert projection.status_code == 200
    assert projection.json()["method"] == "snowball"
    assert projection.json()["order"] == []
