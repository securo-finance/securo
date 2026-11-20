"""Market tickers for provider-synced holdings.

Pluggy reports a B3 symbol in `code` for exchange-traded positions. The sync
turns it into a Yahoo symbol, confirms it with a live quote and then prices
the holding from the market (units stay provider-owned). When detection
fails, the user links a ticker by hand, and later syncs keep that choice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.user import User
from app.providers.base import HoldingData
from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
    set_market_price_provider,
)
from app.providers.pluggy import _b3_market_ticker, _build_holding_data
from app.schemas.asset import AssetBuyCreate, MarketSymbolMatch, MarketSymbolQuote
from app.services import asset_transaction_service
from app.services.connection_service import _sync_holdings
from tests.test_sync_holdings import (  # noqa: F401 - autouse fixture
    _MockProvider,
    _register_mock_provider,
)


@pytest_asyncio.fixture
async def conn(session: AsyncSession, test_user: User) -> BankConnection:
    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="mock",
        external_id="item-b3",
        institution_name="Mock Broker",
        credentials={"item_id": "item-b3"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


class _FakeMarket(MarketPriceProvider):
    name = "fake"

    def __init__(self, quotes: dict[str, MarketSymbolQuote], *, rate_limited: bool = False) -> None:
        self.quotes = quotes
        self.rate_limited = rate_limited
        self.calls: list[str] = []

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        self.calls.append(symbol)
        if self.rate_limited:
            raise MarketPriceRateLimitedError("simulated")
        return self.quotes.get(symbol.upper())


def _quote(symbol: str, price: float, currency: str = "BRL") -> MarketSymbolQuote:
    return MarketSymbolQuote(
        symbol=symbol,
        name=f"{symbol} SA",
        exchange="SAO",
        currency=currency,
        price=price,
        quote_type="EQUITY",
        logo_url=f"https://logos.example/{symbol}.png",
    )


@pytest.fixture
def market():
    fake = _FakeMarket({})
    set_market_price_provider(fake)
    yield fake
    set_market_price_provider(None)


def _b3_holding(
    *,
    external_id: str = "inv-1",
    ticker: Optional[str] = "PSSA3.SA",
    quotable: bool = True,
    quantity: Decimal = Decimal("7"),
    current_value: Decimal = Decimal("300.00"),
) -> HoldingData:
    return HoldingData(
        external_id=external_id,
        name="PSSA3",
        currency="BRL",
        current_value=current_value,
        quantity=quantity,
        ticker=ticker,
        ticker_quotable=quotable,
        metadata={"status": "ACTIVE", "code": "PSSA3", "type": "EQUITY"},
    )


async def _only_asset(session: AsyncSession, user: User) -> Asset:
    rows = await session.execute(select(Asset).where(Asset.user_id == user.id))
    assets = list(rows.scalars().all())
    assert len(assets) == 1
    return assets[0]


async def _today_value(session: AsyncSession, asset: Asset) -> Decimal:
    rows = await session.execute(
        select(AssetValue).where(AssetValue.asset_id == asset.id, AssetValue.date == date.today())
    )
    return Decimal(str(rows.scalar_one().amount))


async def _sync(session: AsyncSession, user: User, conn: BankConnection) -> None:
    await _sync_holdings(session, user.id, conn, conn.credentials)
    await session.commit()


# ---------------------------------------------------------------------------
# Provider: B3 symbol detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inv", "expected"),
    [
        ({"type": "EQUITY", "code": "PSSA3"}, "PSSA3.SA"),
        ({"type": "EQUITY", "code": "GGRC11", "subtype": "REAL_ESTATE_FUND"}, "GGRC11.SA"),
        ({"type": "ETF", "code": "B5P211"}, "B5P211.SA"),
        ({"type": "ETF", "code": "WRLD11"}, "WRLD11.SA"),
        ({"type": "EQUITY", "code": "AAPL34", "subtype": "BDR"}, "AAPL34.SA"),
        ({"type": "EQUITY", "code": "ITUB4F"}, "ITUB4.SA"),
        ({"type": "equity", "code": " pssa3 "}, "PSSA3.SA"),
        ({"type": "EQUITY", "code": "PSSA3", "currencyCode": "BRL"}, "PSSA3.SA"),
        # Not exchange-traded: `code` holds other identifiers.
        ({"type": "FIXED_INCOME", "code": "0001-02"}, None),
        ({"type": "SECURITY", "code": "07.400.588/0001-10"}, None),
        ({"type": "MUTUAL_FUND", "code": "ABCD11"}, None),
        # Exchange-traded but not a B3 symbol, or not in reais.
        ({"type": "EQUITY", "code": "AAPL", "currencyCode": "USD"}, None),
        ({"type": "EQUITY", "code": "PSSA3", "currencyCode": "USD"}, None),
        ({"type": "EQUITY", "code": "PETROBRAS"}, None),
        ({"type": "EQUITY", "code": None}, None),
        ({"type": "EQUITY"}, None),
        ({"code": "PSSA3"}, None),
    ],
)
def test_b3_market_ticker(inv: dict, expected: Optional[str]):
    assert _b3_market_ticker(inv) == expected


def test_build_holding_data_marks_b3_symbol_quotable():
    holding = _build_holding_data(
        {"id": "inv-1", "type": "EQUITY", "code": "PSSA3", "balance": 312.62, "quantity": 7}
    )
    assert holding.ticker == "PSSA3.SA"
    assert holding.ticker_quotable is True
    # `code` stays available in the raw snapshot.
    assert holding.metadata is not None and holding.metadata["code"] == "PSSA3"


def test_build_holding_data_without_symbol_has_no_ticker():
    holding = _build_holding_data(
        {"id": "inv-2", "type": "FIXED_INCOME", "code": "0001-02", "balance": 1000}
    )
    assert holding.ticker is None
    assert holding.ticker_quotable is False


# ---------------------------------------------------------------------------
# Sync: confirmed tickers price the holding from the market
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_ticker_prices_synced_holding_from_market(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]

    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.ticker == "PSSA3.SA"
    assert asset.valuation_method == "market_price"
    assert asset.last_price == Decimal("44.000000")
    assert asset.logo_url == "https://logos.example/PSSA3.SA.png"
    assert asset.ticker_exchange == "SAO"
    # Still provider-owned.
    assert asset.connection_id == conn.id
    assert asset.units == Decimal("7")
    # Today's point is units × quote, not the provider balance.
    assert await _today_value(session, asset) == Decimal("308.00")


@pytest.mark.asyncio
async def test_resync_updates_units_and_keeps_market_valuation(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)

    _MockProvider._holdings = [_b3_holding(quantity=Decimal("9"), current_value=Decimal("390"))]
    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.units == Decimal("9")
    assert asset.valuation_method == "market_price"
    assert await _today_value(session, asset) == Decimal("396.00")
    # Already linked: no second lookup.
    assert market.calls == ["PSSA3.SA"]


@pytest.mark.asyncio
async def test_unknown_ticker_keeps_provider_valuation_and_retries_next_sync(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.ticker is None
    assert asset.valuation_method == "manual"
    assert await _today_value(session, asset) == Decimal("300.00")

    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    await _sync(session, test_user, conn)

    await session.refresh(asset)
    assert asset.ticker == "PSSA3.SA"
    assert asset.valuation_method == "market_price"


@pytest.mark.asyncio
async def test_ticker_in_another_currency_is_not_linked(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 8.00, currency="USD")
    _MockProvider._holdings = [_b3_holding()]

    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.ticker is None
    assert asset.valuation_method == "manual"


@pytest.mark.asyncio
async def test_user_linked_ticker_survives_sync(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)
    asset = await _only_asset(session, test_user)
    asset.ticker = "PSSA3F.SA"
    asset.valuation_method = "market_price"
    asset.last_price = Decimal("43.00")
    await session.commit()
    market.calls.clear()
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)

    await _sync(session, test_user, conn)

    await session.refresh(asset)
    assert asset.ticker == "PSSA3F.SA"
    assert market.calls == []


@pytest.mark.asyncio
async def test_rate_limit_stops_lookups_for_the_rest_of_the_sync(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    market.rate_limited = True
    _MockProvider._holdings = [
        _b3_holding(external_id="a", ticker="PSSA3.SA"),
        _b3_holding(external_id="b", ticker="ITUB4.SA"),
    ]

    await _sync(session, test_user, conn)

    rows = await session.execute(select(Asset).where(Asset.user_id == test_user.id))
    assets = list(rows.scalars().all())
    assert len(assets) == 2
    assert all(a.valuation_method == "manual" and a.ticker is None for a in assets)
    assert len(market.calls) == 1


@pytest.mark.asyncio
async def test_quote_errors_never_fail_the_sync(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    async def boom(symbol: str):
        raise RuntimeError("yahoo down")

    market.get_quote = boom  # type: ignore[method-assign]
    _MockProvider._holdings = [_b3_holding()]

    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.valuation_method == "manual"
    assert await _today_value(session, asset) == Decimal("300.00")


@pytest.mark.asyncio
async def test_non_quotable_ticker_stays_a_label(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    _MockProvider._holdings = [_b3_holding(ticker="VTI", quotable=False)]

    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.ticker == "VTI"
    assert asset.valuation_method == "manual"
    assert market.calls == []


# ---------------------------------------------------------------------------
# Manual link (fallback) and the guard on ticker buys
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def synced_asset(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
) -> Asset:
    _MockProvider._holdings = [_b3_holding(ticker=None, quotable=False)]
    await _sync(session, test_user, conn)
    return await _only_asset(session, test_user)


@pytest.mark.asyncio
async def test_link_ticker_endpoint_links_synced_holding(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, synced_asset: Asset,
    market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)

    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker",
        json={"ticker": " pssa3.sa "},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticker"] == "PSSA3.SA"
    assert body["valuation_method"] == "market_price"
    assert body["last_price"] == pytest.approx(44.0)
    assert body["current_value"] == pytest.approx(308.0)
    assert body["connection_id"] == str(synced_asset.connection_id)


@pytest.mark.asyncio
async def test_link_ticker_endpoint_relinks_to_another_symbol(
    client: AsyncClient, auth_headers: dict, synced_asset: Asset, market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    market.quotes["BBSE3.SA"] = _quote("BBSE3.SA", 37.00)
    await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )

    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "BBSE3.SA"}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "BBSE3.SA"
    assert body["logo_url"] == "https://logos.example/BBSE3.SA.png"


@pytest.mark.asyncio
async def test_link_ticker_endpoint_rejects_unknown_symbol(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, synced_asset: Asset,
    market: _FakeMarket,
):
    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "NOPE3.SA"}, headers=auth_headers
    )

    assert response.status_code == 422
    await session.refresh(synced_asset)
    assert synced_asset.ticker is None
    assert synced_asset.valuation_method == "manual"


@pytest.mark.asyncio
async def test_link_ticker_endpoint_rejects_currency_mismatch(
    client: AsyncClient, auth_headers: dict, synced_asset: Asset, market: _FakeMarket,
):
    market.quotes["AAPL"] = _quote("AAPL", 200.0, currency="USD")

    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "AAPL"}, headers=auth_headers
    )

    assert response.status_code == 422
    assert "USD" in response.json()["detail"]


@pytest.mark.asyncio
async def test_link_ticker_endpoint_rejects_manual_assets(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    test_workspace, market: _FakeMarket,
):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="House",
        type="real_estate",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.commit()

    response = await client.post(
        f"/api/assets/{asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )

    assert response.status_code == 422
    assert market.calls == []


@pytest.mark.asyncio
async def test_link_ticker_endpoint_rejects_blank_ticker(
    client: AsyncClient, auth_headers: dict, synced_asset: Asset, market: _FakeMarket,
):
    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "   "}, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_link_ticker_endpoint_surfaces_rate_limit(
    client: AsyncClient, auth_headers: dict, synced_asset: Asset, market: _FakeMarket,
):
    market.rate_limited = True
    response = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_buy_by_ticker_never_lands_on_a_synced_holding(
    session: AsyncSession, test_user: User, test_workspace, synced_asset: Asset, market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    from app.services import asset_service

    await asset_service.link_market_ticker(session, synced_asset, "PSSA3.SA")
    await session.commit()

    bought = await asset_transaction_service.buy_into_holding(
        session,
        test_workspace.id,
        test_user.id,
        AssetBuyCreate(
            ticker="PSSA3.SA",
            quantity=Decimal("2"),
            price=Decimal("40"),
            date=date(2026, 11, 1),
            group_id=synced_asset.group_id,
        ),
    )

    assert bought.id != synced_asset.id
    await session.refresh(synced_asset)
    assert synced_asset.units == Decimal("7")
