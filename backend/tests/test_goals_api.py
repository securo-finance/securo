import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.fx_rate import FxRate
from app.models.user import User


@pytest.mark.asyncio
async def test_create_goal_manual(client: AsyncClient, auth_headers: dict, test_user: User):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Emergency Fund",
            "target_amount": "10000.00",
            "current_amount": "2500.00",
            "currency": "BRL",
            "tracking_type": "manual",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Emergency Fund"
    assert float(data["target_amount"]) == 10000.00
    assert float(data["current_amount"]) == 2500.00
    assert data["currency"] == "BRL"
    assert data["tracking_type"] == "manual"
    assert data["status"] == "active"
    assert data["percentage"] == 25.0


@pytest.mark.asyncio
async def test_create_goal_with_target_date(client: AsyncClient, auth_headers: dict, test_user: User):
    future_date = (date.today() + timedelta(days=365)).isoformat()
    response = await client.post(
        "/api/goals",
        json={
            "name": "Vacation",
            "target_amount": "5000.00",
            "current_amount": "1000.00",
            "currency": "BRL",
            "target_date": future_date,
            "tracking_type": "manual",
            "icon": "plane",
            "color": "#3B82F6",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["target_date"] == future_date
    assert data["icon"] == "plane"
    assert data["color"] == "#3B82F6"
    assert data["monthly_contribution"] is not None
    assert data["monthly_contribution"] > 0
    assert data["on_track"] is not None


@pytest.mark.asyncio
async def test_create_goal_account_tracking(
    client: AsyncClient, auth_headers: dict, test_user: User, test_account: Account
):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Savings Account Goal",
            "target_amount": "5000.00",
            "currency": "BRL",
            "tracking_type": "account",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_type"] == "account"
    assert data["account_id"] == str(test_account.id)
    # current_amount should reflect account balance (1500.00 from test_account)
    assert float(data["current_amount"]) == 1500.00
    assert data["account_name"] == "Conta Corrente"


@pytest.mark.asyncio
async def test_create_goal_invalid_tracking_type(client: AsyncClient, auth_headers: dict, test_user: User):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Bad Goal",
            "target_amount": "1000.00",
            "tracking_type": "invalid",
        },
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_goals(client: AsyncClient, auth_headers: dict, test_user: User):
    # Create two goals
    await client.post(
        "/api/goals",
        json={"name": "Goal A", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    await client.post(
        "/api/goals",
        json={"name": "Goal B", "target_amount": "2000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )

    response = await client.get("/api/goals", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    names = [g["name"] for g in data]
    assert "Goal A" in names
    assert "Goal B" in names


@pytest.mark.asyncio
async def test_list_goals_filter_by_status(client: AsyncClient, auth_headers: dict, test_user: User):
    # Create an active goal
    resp = await client.post(
        "/api/goals",
        json={"name": "Active Goal", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    # Pause it
    await client.patch(
        f"/api/goals/{goal_id}",
        json={"status": "paused"},
        headers=auth_headers,
    )

    # Filter active only
    response = await client.get("/api/goals?status=active", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    paused_goals = [g for g in data if g["id"] == goal_id]
    assert len(paused_goals) == 0


@pytest.mark.asyncio
async def test_get_goal(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Single Goal", "target_amount": "3000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    response = await client.get(f"/api/goals/{goal_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Single Goal"


@pytest.mark.asyncio
async def test_get_goal_not_found(client: AsyncClient, auth_headers: dict, test_user: User):
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/goals/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_goal(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Update Me", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"name": "Updated Name", "target_amount": "2000.00", "current_amount": "500.00"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert float(data["target_amount"]) == 2000.00
    assert float(data["current_amount"]) == 500.00
    assert data["percentage"] == 25.0


@pytest.mark.asyncio
async def test_update_goal_status(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Status Goal", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    # Pause
    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"status": "paused"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "paused"

    # Complete
    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"status": "completed"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    # Archive
    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"status": "archived"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_update_goal_tracking_type(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Tracking Goal", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    for tracking_type in ("manual", "account", "asset", "net_worth"):
        response = await client.patch(
            f"/api/goals/{goal_id}",
            json={"tracking_type": tracking_type},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["tracking_type"] == tracking_type


@pytest.mark.asyncio
async def test_update_goal_invalid_tracking_type(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Bad Tracking", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"tracking_type": "invalid"},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_goal_invalid_status(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Bad Status", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    response = await client.patch(
        f"/api/goals/{goal_id}",
        json={"status": "invalid_status"},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_goal(client: AsyncClient, auth_headers: dict, test_user: User):
    resp = await client.post(
        "/api/goals",
        json={"name": "Delete Me", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    goal_id = resp.json()["id"]

    response = await client.delete(f"/api/goals/{goal_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify deleted
    response = await client.get(f"/api/goals/{goal_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_goal_not_found(client: AsyncClient, auth_headers: dict, test_user: User):
    fake_id = str(uuid.uuid4())
    response = await client.delete(f"/api/goals/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_goal_summary(client: AsyncClient, auth_headers: dict, test_user: User):
    # Create a few goals
    for name in ["Goal 1", "Goal 2", "Goal 3", "Goal 4"]:
        await client.post(
            "/api/goals",
            json={"name": name, "target_amount": "1000.00", "current_amount": "250.00", "tracking_type": "manual"},
            headers=auth_headers,
        )

    response = await client.get("/api/goals/summary?limit=3", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 3
    for item in data:
        assert "percentage" in item
        assert "monthly_contribution" in item
        assert item["percentage"] == 25.0


@pytest.mark.asyncio
async def test_goal_percentage_100(client: AsyncClient, auth_headers: dict, test_user: User):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Achieved Goal",
            "target_amount": "1000.00",
            "current_amount": "1500.00",
            "tracking_type": "manual",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["percentage"] == 150.0


@pytest.mark.asyncio
async def test_goal_on_track_overdue(client: AsyncClient, auth_headers: dict, test_user: User):
    past_date = (date.today() - timedelta(days=30)).isoformat()
    response = await client.post(
        "/api/goals",
        json={
            "name": "Overdue Goal",
            "target_amount": "5000.00",
            "current_amount": "100.00",
            "target_date": past_date,
            "tracking_type": "manual",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["on_track"] == "overdue"
    assert data["monthly_contribution"] == 0.0


@pytest.mark.asyncio
async def test_goal_on_track_achieved(client: AsyncClient, auth_headers: dict, test_user: User):
    future_date = (date.today() + timedelta(days=365)).isoformat()
    response = await client.post(
        "/api/goals",
        json={
            "name": "Achieved Goal",
            "target_amount": "1000.00",
            "current_amount": "1500.00",
            "target_date": future_date,
            "tracking_type": "manual",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["on_track"] == "achieved"
    assert data["monthly_contribution"] == 0.0


@pytest.mark.asyncio
async def test_goal_ownership_isolation(
    client: AsyncClient, auth_headers: dict, test_user: User, session: AsyncSession
):
    """Goals should not be visible to other users."""
    # Create a goal for test_user
    resp = await client.post(
        "/api/goals",
        json={"name": "My Goal", "target_amount": "1000.00", "tracking_type": "manual"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    goal_id = resp.json()["id"]

    # Create another user
    import bcrypt as _bcrypt

    hashed = _bcrypt.hashpw(b"otherpass123", _bcrypt.gensalt()).decode()
    from app.models.user import User as UserModel
    from app.services.workspace_service import create_personal_workspace_for_user

    other_user = UserModel(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"language": "en", "date_format": "MM/DD/YYYY", "timezone": "UTC", "currency_display": "USD"},
    )
    session.add(other_user)
    await session.flush()
    await create_personal_workspace_for_user(session, other_user)
    await session.commit()

    # Login as other user
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": "other@example.com", "password": "otherpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    other_token = login_resp.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # Other user should not see the goal
    response = await client.get(f"/api/goals/{goal_id}", headers=other_headers)
    assert response.status_code == 404

    # Other user's list should be empty
    response = await client.get("/api/goals", headers=other_headers)
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_goal_with_metadata(client: AsyncClient, auth_headers: dict, test_user: User):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Team Goal",
            "target_amount": "50000.00",
            "tracking_type": "manual",
            "metadata_json": {"department": "Engineering", "tags": ["q2", "hiring"]},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["metadata_json"]["department"] == "Engineering"
    assert "hiring" in data["metadata_json"]["tags"]


@pytest.mark.asyncio
async def test_goal_net_worth_tracking(
    client: AsyncClient, auth_headers: dict, test_user: User, test_account: Account
):
    response = await client.post(
        "/api/goals",
        json={
            "name": "Net Worth Target",
            "target_amount": "100000.00",
            "currency": "BRL",
            "tracking_type": "net_worth",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_type"] == "net_worth"
    # current_amount should reflect total balance (at least test_account balance)
    assert float(data["current_amount"]) > 0


@pytest.mark.asyncio
async def test_goal_asset_tracking(
    client: AsyncClient, auth_headers: dict, test_user: User, session: AsyncSession
):
    """Goal with tracking_type=asset should resolve current_amount from asset value."""
    # Create an asset with a purchase price
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Investment Portfolio",
        type="investment",
        currency="BRL",
        purchase_price=Decimal("50000.00"),
    )
    session.add(asset)
    await session.commit()

    response = await client.post(
        "/api/goals",
        json={
            "name": "Grow Portfolio",
            "target_amount": "100000.00",
            "currency": "BRL",
            "tracking_type": "asset",
            "asset_id": str(asset.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_type"] == "asset"
    assert data["asset_id"] == str(asset.id)
    assert data["asset_name"] == "Investment Portfolio"
    # current_amount should reflect asset value (purchase_price as fallback)
    assert float(data["current_amount"]) == 50000.00
    assert data["percentage"] == 50.0


@pytest.mark.asyncio
async def test_goal_asset_tracking_with_currency_conversion(
    client: AsyncClient, auth_headers: dict, test_user: User, session: AsyncSession
):
    """Asset-linked goal should convert asset value to goal currency."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="EUR Property",
        type="real_estate",
        currency="EUR",
        purchase_price=Decimal("200000.00"),
    )
    session.add(asset)
    await session.commit()

    response = await client.post(
        "/api/goals",
        json={
            "name": "Property Goal",
            "target_amount": "500000.00",
            "currency": "BRL",  # Goal in BRL, asset in EUR
            "tracking_type": "asset",
            "asset_id": str(asset.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    # Should be converted from EUR to BRL (exact rate depends on FX mock)
    assert float(data["current_amount"]) > 0


@pytest.mark.asyncio
async def test_goal_account_tracking_cross_currency(
    client: AsyncClient, auth_headers: dict, test_user: User, test_account: Account, session: AsyncSession
):
    """EUR goal linked to a BRL account should convert account balance to EUR."""
    # Seed FX rates: USD→BRL = 5.0, USD→EUR = 0.9
    # So BRL 1500 → USD 300 → EUR 270
    today = date.today()
    for quote, rate in [("BRL", "5.0"), ("EUR", "0.9")]:
        session.add(FxRate(
            base_currency="USD", quote_currency=quote,
            date=today, rate=Decimal(rate), source="test",
        ))
    await session.commit()

    response = await client.post(
        "/api/goals",
        json={
            "name": "EUR Savings from BRL",
            "target_amount": "5000.00",
            "currency": "EUR",  # Goal in EUR, account in BRL
            "tracking_type": "account",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_type"] == "account"
    assert data["currency"] == "EUR"
    # BRL 1500 / 5.0 * 0.9 = EUR 270
    current = float(data["current_amount"])
    assert current == pytest.approx(270.0, abs=1.0)


@pytest.mark.asyncio
async def test_goal_account_tracking_same_currency(
    client: AsyncClient, auth_headers: dict, test_user: User, test_account: Account
):
    """BRL goal linked to a BRL account should show the exact account balance."""
    response = await client.post(
        "/api/goals",
        json={
            "name": "BRL Account Goal",
            "target_amount": "10000.00",
            "currency": "BRL",  # Same currency as account
            "tracking_type": "account",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["currency"] == "BRL"
    # No conversion needed — should be exact account balance
    assert float(data["current_amount"]) == 1500.00
    assert data["percentage"] == 15.0


@pytest.mark.asyncio
async def test_goal_asset_tracking_eur_goal_usd_asset(
    client: AsyncClient, auth_headers: dict, test_user: User, session: AsyncSession
):
    """EUR goal linked to a USD asset should convert asset value to EUR."""
    # Seed FX rates: USD→EUR = 0.9
    today = date.today()
    session.add(FxRate(
        base_currency="USD", quote_currency="EUR",
        date=today, rate=Decimal("0.9"), source="test",
    ))

    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="US Stock",
        type="investment",
        currency="USD",
        purchase_price=Decimal("8000.00"),
    )
    session.add(asset)
    await session.commit()

    response = await client.post(
        "/api/goals",
        json={
            "name": "EUR Growth from USD",
            "target_amount": "50000.00",
            "currency": "EUR",  # Goal in EUR, asset in USD
            "tracking_type": "asset",
            "asset_id": str(asset.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_type"] == "asset"
    assert data["currency"] == "EUR"
    assert data["asset_name"] == "US Stock"
    # USD 8000 * 0.9 = EUR 7200
    current = float(data["current_amount"])
    assert current == pytest.approx(7200.0, abs=1.0)


@pytest.mark.asyncio
async def test_unauthenticated_access(client: AsyncClient):
    response = await client.get("/api/goals")
    assert response.status_code == 401
