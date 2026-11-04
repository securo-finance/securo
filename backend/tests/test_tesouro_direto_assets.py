from __future__ import annotations

from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.market_price import MarketPriceProvider
from app.schemas.asset import AssetCreate, MarketSymbolMatch, MarketSymbolQuote
from app.services import asset_service
from app.services.asset_service import refresh_all_market_prices


class FakeMarketProvider(MarketPriceProvider):
    name = "fake"

    def __init__(self, quotes: dict[str, MarketSymbolQuote], batch_prices: dict[str, Optional[Decimal]] | None = None):
        self.quotes = {k.upper(): v for k, v in quotes.items()}
        self.batch_prices = {k.upper(): v for k, v in (batch_prices or {}).items()}
        self.bulk_calls = 0

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        return self.quotes.get(symbol.upper())

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        self.bulk_calls += 1
        if self.batch_prices:
            return {symbol.upper(): self.batch_prices.get(symbol.upper()) for symbol in symbols}
        return {
            symbol.upper(): (Decimal(str(self.quotes[symbol.upper()].price)) if symbol.upper() in self.quotes else None)
            for symbol in symbols
        }


def _quote(symbol: str = "TD:ABC12345:2029-03-01", price: float = 15130.10) -> MarketSymbolQuote:
    return MarketSymbolQuote(
        symbol=symbol,
        name="Tesouro Selic 2029",
        exchange="Tesouro Direto",
        currency="BRL",
        price=price,
        quote_type="BOND",
    )


@pytest.mark.asyncio
async def test_create_tesouro_market_price_asset_reuses_market_ledger(
    session: AsyncSession, test_user: User, test_workspace
):
    symbol = "TD:ABC12345:2029-03-01"
    provider = FakeMarketProvider({symbol: _quote(symbol)})
    data = AssetCreate(
        name="Tesouro Selic 2029",
        type="investment",
        currency="USD",
        valuation_method="market_price",
        ticker=symbol,
        units=Decimal("2.5"),
    )

    created = await asset_service.create_asset(
        session, test_workspace.id, test_user.id, data, market_provider=provider
    )

    assert created.valuation_method == "market_price"
    assert created.source == "tesouro_direto"
    assert created.currency == "BRL"
    assert created.ticker == symbol
    assert created.last_price == pytest.approx(15130.10)
    assert created.current_value == pytest.approx(37825.25)
    assert created.value_count == 1
    assert created.average_price == pytest.approx(15130.10)
    assert created.transaction_count == 1

    db_asset = await session.get(Asset, created.id)

    assert db_asset is not None
    assert db_asset.external_metadata is None


@pytest.mark.asyncio
async def test_refresh_all_market_prices_updates_tesouro_symbol_through_market_provider(
    session: AsyncSession, test_user: User, test_workspace
):
    symbol = "TD:ABC12345:2029-03-01"
    created = await asset_service.create_asset(
        session,
        test_workspace.id,
        test_user.id,
        AssetCreate(
            name="Tesouro Selic 2029",
            type="investment",
            valuation_method="market_price",
            ticker=symbol,
            units=Decimal("2"),
        ),
        market_provider=FakeMarketProvider({symbol: _quote(symbol, 15130.10)}),
    )

    refresh_provider = FakeMarketProvider({}, {symbol: Decimal("15200.00")})
    summary = await refresh_all_market_prices(session, market_provider=refresh_provider)

    assert summary == {"refreshed": 1, "skipped": 0, "rate_limited": 0}
    assert refresh_provider.bulk_calls == 1
    db_asset = await session.get(Asset, created.id)

    assert db_asset is not None
    assert db_asset.last_price == Decimal("15200.000000")
    values = (
        await session.execute(
            select(AssetValue).where(AssetValue.asset_id == db_asset.id).order_by(AssetValue.date)
        )
    ).scalars().all()
    assert values[-1].price == Decimal("15200.000000")
    assert values[-1].amount == Decimal("30400.00")
