import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


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
async def test_only_a_loan_can_be_linked_to_a_property_or_vehicle(
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
async def test_a_loan_cannot_link_to_a_property_in_another_workspace(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
):
    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Other household",
        kind="personal",
        created_by_user_id=test_user.id,
    )
    session.add(other_workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=other_workspace.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    home = await _asset(session, test_user, other_workspace, "real_estate")

    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Loan", "type": "loan", "secured_asset_id": str(home.id)},
    )

    assert response.status_code == 400
    assert "same workspace" in response.json()["detail"]


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
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    checking = next(row for row in listed if row["id"] == loan["id"])
    assert checking["current_balance"] == 1000


@pytest.mark.asyncio
async def test_retyping_account_to_loan_resigns_opening_balance_without_balance_update(
    client: AsyncClient,
    auth_headers,
):
    created = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Mortgage", "type": "checking", "balance": 200000, "currency": "EUR"},
    )
    assert created.status_code == 201, created.text

    updated = await client.patch(
        f"/api/accounts/{created.json()['id']}",
        headers=auth_headers,
        json={"type": "loan"},
    )

    assert updated.status_code == 200, updated.text
    accounts = (await client.get("/api/accounts", headers=auth_headers)).json()
    mortgage = next(row for row in accounts if row["id"] == created.json()["id"])
    assert mortgage["current_balance"] == -200000


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


@pytest.mark.asyncio
async def test_retyping_the_linked_property_drops_the_link_and_keeps_the_loan_editable(
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
            json={"name": "Mortgage", "type": "loan", "balance": 1000, "secured_asset_id": str(home.id)},
        )
    ).json()

    retyped_asset = await client.patch(
        f"/api/assets/{home.id}", headers=auth_headers, json={"type": "other"}
    )
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    renamed = await client.patch(
        f"/api/accounts/{loan['id']}", headers=auth_headers, json={"name": "Old mortgage"}
    )

    assert retyped_asset.status_code == 200, retyped_asset.text
    assert next(row for row in listed if row["id"] == loan["id"])["secured_asset_id"] is None
    assert renamed.status_code == 200, renamed.text


@pytest.mark.asyncio
async def test_retyping_a_loan_also_moves_the_opening_balance_date(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
):
    loan = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "Loan", "type": "loan", "balance": 1000, "balance_date": "2026-01-10"},
        )
    ).json()

    retyped = await client.patch(
        f"/api/accounts/{loan['id']}",
        headers=auth_headers,
        json={"type": "checking", "balance_date": "2026-02-20"},
    )

    assert retyped.status_code == 200, retyped.text
    opening = await session.scalar(
        select(Transaction).where(
            Transaction.account_id == uuid.UUID(loan["id"]),
            Transaction.source == "opening_balance",
        )
    )
    assert opening is not None
    assert opening.type == "credit"
    assert opening.date == date(2026, 2, 20)


@pytest.mark.asyncio
async def test_a_car_loan_can_be_linked_to_a_vehicle(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    car = await _asset(session, test_user, test_workspace, "vehicle")

    created = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Car financing", "type": "loan", "balance": 30000, "secured_asset_id": str(car.id)},
    )

    assert created.status_code == 201, created.text
    assert created.json()["secured_asset_id"] == str(car.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("from_type,to_type", [("loan", "checking"), ("checking", "loan")])
async def test_an_account_with_transactions_cannot_switch_to_or_from_a_loan(
    client: AsyncClient,
    auth_headers,
    from_type: str,
    to_type: str,
):
    account = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "Account", "type": from_type, "balance": 1000},
        )
    ).json()
    posted = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Payment",
            "amount": 100,
            "type": "credit",
            "date": app_today().isoformat(),
        },
    )
    assert posted.status_code == 201, posted.text

    retyped = await client.patch(
        f"/api/accounts/{account['id']}", headers=auth_headers, json={"type": to_type}
    )

    assert retyped.status_code == 400
    assert "cannot switch" in retyped.json()["detail"]


@pytest.mark.asyncio
async def test_balance_history_leaves_loans_out(client: AsyncClient, auth_headers):
    month_start = app_today().replace(day=1)
    opened = (month_start - timedelta(days=1)).replace(day=1)
    for body in (
        {"name": "Checking", "type": "checking", "balance": 1000},
        {"name": "Mortgage", "type": "loan", "balance": 200000},
    ):
        created = await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={**body, "balance_date": opened.isoformat()},
        )
        assert created.status_code == 201, created.text

    history = await client.get("/api/dashboard/balance-history", headers=auth_headers)

    assert history.status_code == 200, history.text
    first_day = next(point for point in history.json()["current"] if point["day"] == 1)
    assert first_day["balance"] == 1000
