import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.user import User
from app.models.workspace import Workspace


async def _asset(session: AsyncSession, user: User, workspace: Workspace, asset_type: str) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Home" if asset_type == "real_estate" else "Coins",
        type=asset_type,
        currency="EUR",
        valuation_method="manual",
    )
    session.add(asset)
    await session.commit()
    return asset


@pytest.mark.asyncio
async def test_loan_can_be_linked_to_a_property_and_shows_as_debt(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    home = await _asset(session, test_user, test_workspace, "real_estate")

    created = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Mortgage",
            "type": "loan",
            "balance": 200000,
            "currency": "EUR",
            "secured_asset_id": str(home.id),
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["secured_asset_id"] == str(home.id)
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    loan = next(row for row in listed if row["id"] == created.json()["id"])
    assert loan["current_balance"] == -200000


@pytest.mark.asyncio
async def test_only_a_loan_can_be_linked_to_real_estate(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    home = await _asset(session, test_user, test_workspace, "real_estate")
    coins = await _asset(session, test_user, test_workspace, "crypto")

    not_a_loan = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Checking", "type": "checking", "secured_asset_id": str(home.id)},
    )
    not_a_property = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Loan", "type": "loan", "secured_asset_id": str(coins.id)},
    )
    unknown = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Loan", "type": "loan", "secured_asset_id": str(uuid.uuid4())},
    )

    assert not_a_loan.status_code == 400
    assert not_a_property.status_code == 400
    assert unknown.status_code == 400


@pytest.mark.asyncio
async def test_a_loan_can_be_linked_unlinked_and_retyped(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    home = await _asset(session, test_user, test_workspace, "real_estate")
    loan = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "Loan", "type": "loan", "balance": 1000},
        )
    ).json()

    linked = await client.patch(
        f"/api/accounts/{loan['id']}",
        headers=auth_headers,
        json={"secured_asset_id": str(home.id)},
    )
    unlinked = await client.patch(
        f"/api/accounts/{loan['id']}", headers=auth_headers, json={"secured_asset_id": None}
    )
    relinked = await client.patch(
        f"/api/accounts/{loan['id']}",
        headers=auth_headers,
        json={"secured_asset_id": str(home.id)},
    )
    retyped = await client.patch(
        f"/api/accounts/{loan['id']}", headers=auth_headers, json={"type": "checking"}
    )

    assert linked.json()["secured_asset_id"] == str(home.id)
    assert unlinked.json()["secured_asset_id"] is None
    assert relinked.status_code == 200
    assert retyped.status_code == 200
    assert retyped.json()["secured_asset_id"] is None


@pytest.mark.asyncio
async def test_a_bank_connected_account_cannot_become_a_loan(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    from datetime import datetime, timezone

    from app.models.account import Account
    from app.models.bank_connection import BankConnection

    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Lender",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=connection.id,
        external_id="acc-1",
        name="Lender account",
        type="checking",
        balance=-1000,
        currency="EUR",
    )
    session.add(account)
    await session.commit()

    response = await client.patch(
        f"/api/accounts/{account.id}", headers=auth_headers, json={"type": "loan"}
    )

    assert response.status_code == 400
    assert "cannot be loans" in response.json()["detail"]
