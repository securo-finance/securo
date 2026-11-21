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

from app.models.asset import PROVIDER_VALUE_METADATA_KEY, Asset
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
    assert conn.credentials is not None
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
async def test_a_database_error_while_linking_stays_with_that_holding(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket,
    monkeypatch: pytest.MonkeyPatch,
):
    from sqlalchemy import text

    from app.services import asset_service

    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    market.quotes["BBSE3.SA"] = _quote("BBSE3.SA", 37.00)
    real_apply = asset_service._apply_price_to_asset

    async def failing_apply(session_, asset, price, **kwargs):
        if asset.ticker == "PSSA3.SA":
            # A real statement error, after the link has set the asset's fields.
            await session_.execute(text("SELECT * FROM no_such_table"))
        await real_apply(session_, asset, price, **kwargs)

    monkeypatch.setattr(asset_service, "_apply_price_to_asset", failing_apply)
    _MockProvider._holdings = [
        _b3_holding(),
        _b3_holding(external_id="inv-2", ticker="BBSE3.SA", current_value=Decimal("250.00")),
    ]

    await _sync(session, test_user, conn)

    rows = await session.execute(select(Asset).where(Asset.user_id == test_user.id))
    by_ext = {a.external_id: a for a in rows.scalars().all()}
    failed, linked = by_ext["inv-1"], by_ext["inv-2"]
    # The failed link left nothing behind, so the next sync retries it.
    assert failed.ticker is None
    assert failed.valuation_method == "manual"
    assert failed.last_price is None
    assert await _today_value(session, failed) == Decimal("300.00")
    # The rest of the sync carried on.
    assert linked.ticker == "BBSE3.SA"
    assert linked.valuation_method == "market_price"
    assert await _today_value(session, linked) == Decimal("259.00")

    monkeypatch.setattr(asset_service, "_apply_price_to_asset", real_apply)
    await _sync(session, test_user, conn)
    await session.refresh(failed)
    assert failed.ticker == "PSSA3.SA"
    assert failed.valuation_method == "market_price"


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


@pytest.mark.asyncio
async def test_linked_holding_gets_average_price_from_the_reported_trades(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    from app.providers.base import HoldingTradeData
    from app.services.asset_service import get_asset

    market.quotes["ITUB4.SA"] = _quote("ITUB4.SA", 40.00)
    holding = _b3_holding(ticker="ITUB4.SA")
    holding.trades = [
        HoldingTradeData(external_id="b1", kind="buy", date=date(2026, 10, 22),
                         quantity=Decimal("2"), price=Decimal("48.59"), fee=Decimal("0.22")),
        HoldingTradeData(external_id="b2", kind="buy", date=date(2026, 10, 28),
                         quantity=Decimal("5"), price=Decimal("39.85"), fee=Decimal("0.09")),
    ]
    _MockProvider._holdings = [holding]

    await _sync(session, test_user, conn)

    asset = await _only_asset(session, test_user)
    assert asset.valuation_method == "market_price"
    assert asset.units == Decimal("7")
    # (2 x 48.59 + 0.22 + 5 x 39.85 + 0.09) / 7
    assert Decimal(str(asset.average_price)).quantize(Decimal("0.01")) == Decimal("42.39")
    read = await get_asset(session, asset.id, asset.workspace_id)
    assert read is not None
    assert read.current_value == pytest.approx(280.0)
    assert read.gain_loss == pytest.approx(280.0 - 296.74, abs=0.01)


@pytest.mark.parametrize(
    ("inv", "expected"),
    [
        ({"type": "EQUITY", "subtype": "STOCK"}, "stock"),
        ({"type": "EQUITY", "subtype": "BDR"}, "stock"),
        ({"type": "EQUITY", "subtype": "REAL_ESTATE_FUND"}, "fund"),
        ({"type": "ETF"}, "etf"),
        ({"type": "FIXED_INCOME", "subtype": "CDB"}, None),
        ({"type": "MUTUAL_FUND"}, None),
        ({"type": "SECURITY", "subtype": "PGBL"}, None),
        # An unexpected subtype reads as unknown instead of failing the sync.
        ({"type": "EQUITY", "subtype": 123}, "stock"),
        ({"type": "EQUITY", "subtype": None}, "stock"),
    ],
)
def test_exchange_traded_positions_get_their_asset_type(inv: dict, expected: Optional[str]):
    holding = _build_holding_data({"id": "x", "balance": 1, **inv})
    assert holding.asset_type == expected


# ---------------------------------------------------------------------------
# Snapshot price, raw labels and the way back to the provider's valuation
# ---------------------------------------------------------------------------


async def _today_row(session: AsyncSession, asset: Asset) -> AssetValue:
    rows = await session.execute(
        select(AssetValue).where(AssetValue.asset_id == asset.id, AssetValue.date == date.today())
    )
    return rows.scalar_one()


@pytest.mark.asyncio
async def test_market_priced_snapshot_stores_the_quote_it_was_valued_at(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)
    _MockProvider._holdings = [_b3_holding(quantity=Decimal("9"), current_value=Decimal("390"))]
    await _sync(session, test_user, conn)

    row = await _today_row(session, await _only_asset(session, test_user))
    # With the per-share price the history rebuild recomputes the point from
    # the ledger quantity instead of keeping a baked amount.
    assert Decimal(str(row.amount)) == Decimal("396.00")
    assert row.price == Decimal("44.000000")


@pytest.mark.asyncio
async def test_provider_valued_snapshot_has_no_price(
    session: AsyncSession, test_user: User, conn: BankConnection, market: _FakeMarket
):
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)

    row = await _today_row(session, await _only_asset(session, test_user))
    assert Decimal(str(row.amount)) == Decimal("300.00")
    assert row.price is None


@pytest.mark.asyncio
async def test_raw_label_never_replaces_a_ticker_the_user_linked(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    conn: BankConnection, market: _FakeMarket,
):
    # A provider reporting raw broker symbols (not quotable): its label is
    # VTI, the user prices the holding with VOO.
    _MockProvider._holdings = [_b3_holding(ticker="VTI", quotable=False)]
    await _sync(session, test_user, conn)
    asset = await _only_asset(session, test_user)
    market.quotes["VOO"] = _quote("VOO", 500.00)
    response = await client.post(
        f"/api/assets/{asset.id}/link-ticker", json={"ticker": "VOO"}, headers=auth_headers
    )
    assert response.status_code == 200, response.text

    await _sync(session, test_user, conn)

    await session.refresh(asset)
    assert asset.ticker == "VOO"
    assert asset.logo_url == "https://logos.example/VOO.png"
    assert asset.valuation_method == "market_price"


@pytest.mark.asyncio
async def test_unlink_returns_an_auto_linked_holding_to_the_provider_value(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    conn: BankConnection, market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)
    asset = await _only_asset(session, test_user)
    assert asset.valuation_method == "market_price"

    response = await client.delete(f"/api/assets/{asset.id}/link-ticker", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valuation_method"] == "manual"
    assert body["last_price"] is None
    # The provider's figure is back at once, not units × quote (308).
    assert body["current_value"] == pytest.approx(300.0)
    await session.refresh(asset)
    row = await _today_row(session, asset)
    assert Decimal(str(row.amount)) == Decimal("300.00")
    assert row.price is None


@pytest.mark.asyncio
async def test_unlink_restores_the_provider_value_of_a_holding_synced_before_the_key(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, synced_asset: Asset,
    market: _FakeMarket,
):
    # A holding last synced before the provider value was kept in its
    # metadata: today's point (300) is the only record of the provider's figure.
    synced_asset.external_metadata = {
        key: value
        for key, value in (synced_asset.external_metadata or {}).items()
        if key != PROVIDER_VALUE_METADATA_KEY
    }
    await session.commit()
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)

    linked = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )
    assert linked.json()["current_value"] == pytest.approx(308.0)

    response = await client.delete(f"/api/assets/{synced_asset.id}/link-ticker", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["current_value"] == pytest.approx(300.0)
    await session.refresh(synced_asset)
    row = await _today_row(session, synced_asset)
    assert Decimal(str(row.amount)) == Decimal("300.00")
    assert row.price is None


@pytest.mark.asyncio
async def test_a_later_manual_value_is_not_kept_as_the_provider_value(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, synced_asset: Asset,
    market: _FakeMarket,
):
    # A holding synced before the key, then valued by hand after the sync:
    # the manual 999 is the newest point, but not the provider's figure.
    synced_asset.external_metadata = {
        key: value
        for key, value in (synced_asset.external_metadata or {}).items()
        if key != PROVIDER_VALUE_METADATA_KEY
    }
    session.add(
        AssetValue(
            asset_id=synced_asset.id,
            amount=Decimal("999.00"),
            date=date.fromordinal(date.today().toordinal() + 1),
            source="manual",
        )
    )
    await session.commit()
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)

    linked = await client.post(
        f"/api/assets/{synced_asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )
    assert linked.status_code == 200, linked.text

    await session.refresh(synced_asset)
    metadata = synced_asset.external_metadata or {}
    assert Decimal(metadata[PROVIDER_VALUE_METADATA_KEY]) == Decimal("300")


@pytest.mark.asyncio
async def test_an_unlinked_holding_is_not_linked_again_by_the_sync(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    conn: BankConnection, market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)
    asset = await _only_asset(session, test_user)
    response = await client.delete(f"/api/assets/{asset.id}/link-ticker", headers=auth_headers)
    assert response.status_code == 200
    # The endpoint wrote through its own session; reload this one's copy.
    await session.refresh(asset)

    _MockProvider._holdings = [_b3_holding(current_value=Decimal("310.00"))]
    await _sync(session, test_user, conn)

    await session.refresh(asset)
    assert asset.valuation_method == "manual"
    assert asset.last_price is None
    assert await _today_value(session, asset) == Decimal("310.00")
    # Only the first sync asked for a quote.
    assert market.calls == ["PSSA3.SA"]


@pytest.mark.asyncio
async def test_an_unlinked_holding_can_be_linked_again_by_hand(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    conn: BankConnection, market: _FakeMarket,
):
    market.quotes["PSSA3.SA"] = _quote("PSSA3.SA", 44.00)
    _MockProvider._holdings = [_b3_holding()]
    await _sync(session, test_user, conn)
    asset = await _only_asset(session, test_user)
    await client.delete(f"/api/assets/{asset.id}/link-ticker", headers=auth_headers)

    response = await client.post(
        f"/api/assets/{asset.id}/link-ticker", json={"ticker": "PSSA3.SA"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["valuation_method"] == "market_price"
    assert response.json()["current_value"] == pytest.approx(308.0)


@pytest.mark.asyncio
async def test_unlink_endpoint_rejects_manual_assets(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User,
    test_workspace,
):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="PETR4",
        type="stock",
        currency="BRL",
        ticker="PETR4.SA",
        valuation_method="market_price",
    )
    session.add(asset)
    await session.commit()

    response = await client.delete(f"/api/assets/{asset.id}/link-ticker", headers=auth_headers)

    assert response.status_code == 422
    await session.refresh(asset)
    assert asset.valuation_method == "market_price"
