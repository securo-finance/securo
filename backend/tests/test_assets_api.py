import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.asset import MarketSymbolQuote


@pytest_asyncio.fixture
async def test_asset_api(session: AsyncSession, test_user: User) -> Asset:
    """Create a test asset for API tests."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="API Test House",
        type="real_estate",
        currency="BRL",
        valuation_method="manual",
        purchase_price=Decimal("300000.00"),
        purchase_date=date(2025, 1, 1),
        position=0,
    )
    session.add(asset)
    await session.flush()

    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=asset.id,
        amount=Decimal("350000.00"),
        date=date(2026, 1, 1),
        source="manual",
    )
    session.add(v)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_list_assets(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.get("/api/assets", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(a["name"] == "API Test House" for a in data)


@pytest.mark.asyncio
async def test_get_asset(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.get(f"/api/assets/{test_asset_api.id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "API Test House"
    assert data["current_value"] == 350000.0
    assert data["gain_loss"] == 50000.0


@pytest.mark.asyncio
async def test_get_asset_not_found(client: AsyncClient, auth_headers: dict):
    response = await client.get(f"/api/assets/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_asset(client: AsyncClient, auth_headers: dict):
    response = await client.post("/api/assets", headers=auth_headers, json={
        "name": "New Car",
        "type": "vehicle",
        "currency": "BRL",
        "current_value": 80000,
        "purchase_price": 75000,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Car"
    assert data["current_value"] == 80000.0
    assert data["value_count"] == 1


@pytest.mark.asyncio
async def test_create_asset_with_external_id_returns_it_via_rest(
    client: AsyncClient, auth_headers: dict
):
    response = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={
            "name": "Exterior ETF",
            "type": "investment",
            "currency": "USD",
            "current_value": 1250,
            "external_id": "  portfolio-asset-1  ",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["external_id"] == "portfolio-asset-1"
    assert created["source"] == "manual"

    detail = await client.get(f"/api/assets/{created['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["external_id"] == "portfolio-asset-1"

    listed = await client.get("/api/assets", headers=auth_headers)
    assert listed.status_code == 200
    assert any(
        asset["id"] == created["id"]
        and asset["external_id"] == "portfolio-asset-1"
        for asset in listed.json()
    )


@pytest.mark.asyncio
async def test_create_market_price_asset_with_external_id_via_rest(
    client: AsyncClient, auth_headers: dict
):
    provider = AsyncMock()
    provider.get_quote.return_value = MarketSymbolQuote(
        symbol="VOO",
        name="Vanguard S&P 500 ETF",
        exchange="NYSE Arca",
        currency="USD",
        price=625.50,
        quote_type="ETF",
    )
    payload = {
        "name": "Vanguard S&P 500 ETF",
        "type": "investment",
        "valuation_method": "market_price",
        "ticker": "VOO",
        "units": 3.5,
        "external_id": "portfolio-voo",
    }

    with patch(
        "app.services.asset_service.get_market_price_provider",
        return_value=provider,
    ):
        first = await client.post("/api/assets", headers=auth_headers, json=payload)
        second = await client.post("/api/assets", headers=auth_headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert first.json()["external_id"] == "portfolio-voo"
    assert first.json()["source"] == "yfinance"
    assert first.json()["ticker"] == "VOO"


@pytest.mark.asyncio
async def test_create_asset_is_idempotent_by_external_id_within_workspace(
    client: AsyncClient, auth_headers: dict
):
    payload = {
        "name": "Synced Holding",
        "type": "investment",
        "currency": "USD",
        "current_value": 500,
        "external_id": "portfolio-stable-id",
    }

    first = await client.post("/api/assets", headers=auth_headers, json=payload)
    second = await client.post("/api/assets", headers=auth_headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["value_count"] == 1

    listed = await client.get("/api/assets", headers=auth_headers)
    matching = [
        asset
        for asset in listed.json()
        if asset["external_id"] == "portfolio-stable-id"
    ]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_same_external_id_is_allowed_in_different_workspaces(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Other portfolio",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
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
    await session.commit()

    payload = {
        "name": "Shared external key",
        "type": "investment",
        "currency": "USD",
        "external_id": "same-provider-id",
    }
    first_headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    second_headers = {**auth_headers, "X-Workspace-Id": str(other_workspace.id)}

    first = await client.post("/api/assets", headers=first_headers, json=payload)
    second = await client.post("/api/assets", headers=second_headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_update_asset(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.patch(
        f"/api/assets/{test_asset_api.id}",
        headers=auth_headers,
        json={"name": "Updated House"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated House"


@pytest.mark.asyncio
async def test_delete_asset(client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="To Delete API",
        type="other",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.commit()

    response = await client.delete(f"/api/assets/{asset.id}", headers=auth_headers)
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_list_asset_values(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.get(f"/api/assets/{test_asset_api.id}/values", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_add_asset_value(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.post(
        f"/api/assets/{test_asset_api.id}/values",
        headers=auth_headers,
        json={"amount": 400000, "date": "2026-03-01"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["amount"] == 400000.0
    assert data["source"] == "manual"


@pytest.mark.asyncio
async def test_delete_asset_value(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_asset_api: Asset,
):
    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=test_asset_api.id,
        amount=Decimal("999.00"),
        date=date.today(),
        source="manual",
    )
    session.add(v)
    await session.commit()

    response = await client.delete(f"/api/assets/values/{v.id}", headers=auth_headers)
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_get_value_trend(client: AsyncClient, auth_headers: dict, test_asset_api: Asset):
    response = await client.get(f"/api/assets/{test_asset_api.id}/value-trend", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_unauthenticated_access(client: AsyncClient):
    response = await client.get("/api/assets")
    assert response.status_code == 401
