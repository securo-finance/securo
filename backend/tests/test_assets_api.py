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
from app.models.fx_rate import FxRate
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


class TestAssetValuePrecision:
    """FX conversion of asset values keeps price-field precision (issue #622).

    A low-priced market asset held by a user in another currency must not
    vanish from the primary-currency total: the converted value is rounded at
    the asset price precision (six decimals), not the monetary two.
    """

    @pytest_asyncio.fixture
    async def eur_rate(self, session: AsyncSession, clean_db) -> FxRate:
        """USD -> EUR 0.92, same value the fx_rates fixture uses."""
        fx = FxRate(
            base_currency="USD",
            quote_currency="EUR",
            date=date.today(),
            rate=Decimal("0.9200000000"),
            source="test",
        )
        session.add(fx)
        await session.commit()
        await session.refresh(fx)
        return fx

    @pytest_asyncio.fixture
    async def eur_user(
        self, session: AsyncSession, test_user: User
    ) -> User:
        """The test user reads the portfolio in EUR, not BRL."""
        test_user.preferences = {**(test_user.preferences or {}), "currency_display": "EUR"}
        await session.commit()
        return test_user

    @pytest.mark.asyncio
    async def test_list_keeps_sub_cent_value_in_primary_currency(
        self,
        client: AsyncClient,
        auth_headers: dict,
        session: AsyncSession,
        eur_user: User,
        test_workspace: Workspace,
        eur_rate: FxRate,
    ):
        # One unit of a low-priced coin, worth a fraction of a cent.
        asset = Asset(
            id=uuid.uuid4(),
            user_id=eur_user.id,
            workspace_id=test_workspace.id,
            name="Coin A",
            type="investment",
            currency="USD",
            units=Decimal("1"),
            valuation_method="market_price",
            purchase_price=Decimal("0.00"),
            last_price=Decimal("0.002087"),
            ticker="COIN-USD",
            position=0,
        )
        session.add(asset)
        await session.commit()

        headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
        response = await client.get("/api/assets", headers=headers)

        assert response.status_code == 200
        entry = next(a for a in response.json() if a["id"] == str(asset.id))
        # 0.002087 USD * 0.92 = 0.001920 EUR at price precision. The
        # two-decimal quantization the issue reports returned 0.0 here.
        assert entry["current_value"] == pytest.approx(0.002087)
        assert entry["current_value_primary"] == pytest.approx(0.00192)
        assert entry["gain_loss"] == pytest.approx(0.002087)
        assert entry["gain_loss_primary"] == pytest.approx(0.00192)

    @pytest.mark.asyncio
    async def test_values_at_total_keeps_sub_cent_assets(
        self,
        session: AsyncSession,
        eur_user: User,
        test_workspace: Workspace,
        eur_rate: FxRate,
    ):
        """get_asset_values_at (the rollup behind the dashboard total and the
        trend chart) must not drop low-priced holdings from the primary
        total: each converted amount keeps six decimals, so the sum of
        sub-cent values is correct. 2 x 0.002087 USD = 0.004174, and
        0.002087 x 0.92 = 0.001920 per coin. Before the fix each asset
        converted to 0.0 separately and the primary total was 0.0."""
        from app.services.asset_service import get_asset_values_at

        for i in range(2):
            session.add(
                Asset(
                    id=uuid.uuid4(),
                    user_id=eur_user.id,
                    workspace_id=test_workspace.id,
                    name=f"Coin {i}",
                    type="investment",
                    currency="USD",
                    units=Decimal("1"),
                    valuation_method="market_price",
                    last_price=Decimal("0.002087"),
                    ticker="COIN-USD",
                    position=i,
                )
            )
        await session.commit()

        totals, primary_total = await get_asset_values_at(
            session,
            test_workspace.id,
            primary_currency="EUR",
            by_workspace=True,
        )

        assert totals["USD"] == pytest.approx(0.004174)
        assert primary_total == pytest.approx(0.00384)
class TestAssetListMissingFxRate:
    """issue #574: an asset held in a currency with no resolvable FX rate
    must not be stamped with a fake 1:1 primary value. The row keeps its
    native amount, current_value_primary stays NULL, and the frontend keeps
    the asset out of primary-currency totals instead of summing raw foreign
    amounts. A sibling asset whose currency has a rate still converts, so
    the list endpoint stays one consistent view."""

    @pytest_asyncio.fixture
    async def usd_user(
        self, session: AsyncSession, test_user: User
    ) -> User:
        """The test user reads the portfolio in USD, not BRL."""
        test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
        await session.commit()
        return test_user

    @pytest.mark.asyncio
    async def test_list_converts_only_assets_that_have_a_rate(
        self,
        client: AsyncClient,
        auth_headers: dict,
        session: AsyncSession,
        usd_user: User,
        test_workspace: Workspace,
    ):
        session.add(FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="EUR",
            date=date.today(),
            rate=Decimal("0.9200000000"),
            source="test",
        ))
        eur_asset = Asset(
            id=uuid.uuid4(),
            user_id=usd_user.id,
            workspace_id=test_workspace.id,
            name="Fund",
            type="investment",
            currency="EUR",
            units=Decimal("1000"),
            valuation_method="manual",
            purchase_price=Decimal("1000"),
            position=0,
        )
        gbp_asset = Asset(
            id=uuid.uuid4(),
            user_id=usd_user.id,
            workspace_id=test_workspace.id,
            name="Cash",
            type="cash",
            currency="GBP",
            units=Decimal("500"),
            valuation_method="manual",
            purchase_price=Decimal("500"),
            position=1,
        )
        session.add_all([eur_asset, gbp_asset])
        await session.commit()

        headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
        response = await client.get("/api/assets", headers=headers)

        assert response.status_code == 200
        entries = {a["id"]: a for a in response.json()}
        # With a rate (USD -> EUR 0.92, so EUR -> USD 1/0.92): converted at
        # price precision, 1000 / 0.92 = 1086.956522.
        eur = entries[str(eur_asset.id)]
        assert eur["current_value_primary"] == pytest.approx(1086.956522, rel=1e-6)
        # Without a rate for GBP: left NULL instead of the raw 500.0 the old
        # code stamped at a fake 1:1 rate.
        gbp = entries[str(gbp_asset.id)]
        assert gbp["current_value"] == pytest.approx(500.0)
        assert gbp["current_value_primary"] is None
        assert gbp["gain_loss_primary"] is None

    @pytest.mark.asyncio
    async def test_refresh_price_leaves_primary_null_without_a_rate(
        self,
        client: AsyncClient,
        auth_headers: dict,
        session: AsyncSession,
        usd_user: User,
        test_workspace: Workspace,
    ):
        """The refresh endpoint stamps the primary fields on its own, so it
        must apply the same rule as the list: no GBP rate, no fake value."""
        asset = Asset(
            id=uuid.uuid4(),
            user_id=usd_user.id,
            workspace_id=test_workspace.id,
            name="Listed",
            type="stock",
            currency="GBP",
            ticker="LSE",
            units=Decimal("10"),
            valuation_method="market_price",
            purchase_price=Decimal("40"),
            position=0,
        )
        session.add(asset)
        session.add(AssetValue(
            id=uuid.uuid4(),
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("500"),
            price=Decimal("50"),
            date=date.today(),
            source="sync",
        ))
        await session.commit()

        headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
        with patch(
            "app.services.asset_service.refresh_market_price_asset",
            new=AsyncMock(return_value=True),
        ):
            response = await client.post(
                f"/api/assets/{asset.id}/refresh-price", headers=headers
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["current_value"] is not None
        assert body["gain_loss"] is not None
        assert body["current_value_primary"] is None
        assert body["gain_loss_primary"] is None
