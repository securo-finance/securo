"""Coverage-focused API tests for app/api/assets.py.

Covers the market search/quote proxy endpoints (with the provider mocked),
the single-asset price refresh branches, portfolio-trend, value-trend, and
the 404 branches on the value sub-resources.
"""

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
from app.providers.market_price import MarketPriceRateLimitedError
from app.schemas.asset import MarketSymbolMatch, MarketSymbolQuote


@pytest_asyncio.fixture
async def manual_asset(session: AsyncSession, test_user: User) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Cov House",
        type="real_estate",
        currency="USD",
        valuation_method="manual",
        purchase_price=Decimal("100000.00"),
        purchase_date=date(2024, 1, 1),
        position=0,
    )
    session.add(asset)
    await session.flush()
    session.add(
        AssetValue(
            id=uuid.uuid4(),
            asset_id=asset.id,
            amount=Decimal("120000.00"),
            date=date(2025, 1, 1),
            source="manual",
        )
    )
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest_asyncio.fixture
async def market_asset(session: AsyncSession, test_user: User) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="AAPL Holding",
        type="stock",
        currency="USD",
        valuation_method="market_price",
        ticker="AAPL",
        units=Decimal("10"),
        position=0,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


# ---------------------------------------------------------------------------
# market search / quote
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_market_search_success(client: AsyncClient, auth_headers, test_user):
    fake = AsyncMock()
    fake.search = AsyncMock(
        return_value=[MarketSymbolMatch(symbol="AAPL", name="Apple Inc.", exchange="NMS")]
    )
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/search?q=apple", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_market_search_rate_limited_429(client: AsyncClient, auth_headers, test_user):
    fake = AsyncMock()
    fake.search = AsyncMock(side_effect=MarketPriceRateLimitedError("slow down"))
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/search?q=ab", headers=auth_headers)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_market_search_generic_error_returns_empty(
    client: AsyncClient, auth_headers, test_user
):
    fake = AsyncMock()
    fake.search = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/search?q=ab", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_market_search_validation_422(client: AsyncClient, auth_headers, test_user):
    # Missing required q param.
    resp = await client.get("/api/assets/market/search", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_market_quote_success(client: AsyncClient, auth_headers, test_user):
    fake = AsyncMock()
    fake.get_quote = AsyncMock(
        return_value=MarketSymbolQuote(symbol="AAPL", currency="USD", price=190.5)
    )
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/quote?symbol=AAPL", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["price"] == 190.5


@pytest.mark.asyncio
async def test_market_quote_not_found_404(client: AsyncClient, auth_headers, test_user):
    fake = AsyncMock()
    fake.get_quote = AsyncMock(return_value=None)
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/quote?symbol=NOPE", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_market_quote_rate_limited_429(client: AsyncClient, auth_headers, test_user):
    fake = AsyncMock()
    fake.get_quote = AsyncMock(side_effect=MarketPriceRateLimitedError("slow down"))
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/quote?symbol=AAPL", headers=auth_headers)
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# refresh-price
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_price_asset_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(f"/api/assets/{uuid.uuid4()}/refresh-price", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_refresh_price_non_market_asset_422(client: AsyncClient, auth_headers, manual_asset):
    resp = await client.post(f"/api/assets/{manual_asset.id}/refresh-price", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_refresh_price_success(client: AsyncClient, auth_headers, market_asset):
    with patch(
        "app.services.asset_service.refresh_market_price_asset",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.post(
            f"/api/assets/{market_asset.id}/refresh-price", headers=auth_headers
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(market_asset.id)


@pytest.mark.asyncio
async def test_refresh_price_provider_failure_502(client: AsyncClient, auth_headers, market_asset):
    with patch(
        "app.services.asset_service.refresh_market_price_asset",
        new=AsyncMock(return_value=False),
    ):
        resp = await client.post(
            f"/api/assets/{market_asset.id}/refresh-price", headers=auth_headers
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_refresh_price_rate_limited_429(client: AsyncClient, auth_headers, market_asset):
    with patch(
        "app.services.asset_service.refresh_market_price_asset",
        new=AsyncMock(side_effect=MarketPriceRateLimitedError("slow down")),
    ):
        resp = await client.post(
            f"/api/assets/{market_asset.id}/refresh-price", headers=auth_headers
        )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# list / create / get / update / delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_assets_include_archived(client: AsyncClient, auth_headers, manual_asset):
    resp = await client.get("/api/assets?include_archived=true", headers=auth_headers)
    assert resp.status_code == 200
    assert any(a["id"] == str(manual_asset.id) for a in resp.json())


@pytest.mark.asyncio
async def test_create_then_update_then_delete_asset(client: AsyncClient, auth_headers, test_user):
    create = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={"name": "Boat", "type": "other", "currency": "USD", "current_value": 5000},
    )
    assert create.status_code == 201, create.text
    asset_id = create.json()["id"]

    upd = await client.patch(
        f"/api/assets/{asset_id}",
        headers=auth_headers,
        json={"name": "Yacht", "is_archived": True},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Yacht"

    delete = await client.delete(f"/api/assets/{asset_id}", headers=auth_headers)
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_get_asset_success(client: AsyncClient, auth_headers, manual_asset):
    resp = await client.get(f"/api/assets/{manual_asset.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Cov House"


@pytest.mark.asyncio
async def test_get_asset_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/assets/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_refresh_price_fx_conversion(
    client: AsyncClient, auth_headers, market_asset, session
):
    # Seed a USD value so the refreshed asset has a current_value the
    # endpoint converts into the BRL primary currency (FX branch).
    session.add(
        AssetValue(
            id=uuid.uuid4(),
            asset_id=market_asset.id,
            amount=Decimal("1905.00"),
            date=date(2026, 1, 1),
            source="market",
        )
    )
    await session.commit()
    with patch(
        "app.services.asset_service.refresh_market_price_asset",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.post(
            f"/api/assets/{market_asset.id}/refresh-price", headers=auth_headers
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_update_asset_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.patch(
        f"/api/assets/{uuid.uuid4()}", headers=auth_headers, json={"name": "X"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_asset_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.delete(f"/api/assets/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# values + trends
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_values_unknown_asset_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(f"/api/assets/{uuid.uuid4()}/values", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_value_unknown_asset_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        f"/api/assets/{uuid.uuid4()}/values",
        headers=auth_headers,
        json={"amount": 100, "date": "2026-01-01"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_value_trend_unknown_asset_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.get(
        f"/api/assets/{uuid.uuid4()}/value-trend?months=6", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_value_not_found_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.delete(f"/api/assets/values/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_portfolio_trend(client: AsyncClient, auth_headers, manual_asset):
    resp = await client.get("/api/assets/portfolio-trend", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_add_value_then_value_trend(client: AsyncClient, auth_headers, manual_asset):
    add = await client.post(
        f"/api/assets/{manual_asset.id}/values",
        headers=auth_headers,
        json={"amount": 130000, "date": "2026-02-01"},
    )
    assert add.status_code == 201, add.text

    trend = await client.get(f"/api/assets/{manual_asset.id}/value-trend", headers=auth_headers)
    assert trend.status_code == 200
    assert isinstance(trend.json(), list)
