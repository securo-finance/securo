"""Tests for market-priced assets (yfinance-backed valuation).

These tests stub the MarketPriceProvider so nothing hits the network —
yfinance flakes often enough in CI that any test depending on real
Yahoo responses would be permanently yellow. The stub verifies the
service wiring: create, refresh, upsert-today-value, and rate-limit
short-circuit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
)
from app.schemas.asset import (
    AssetCreate,
    MarketSymbolMatch,
    MarketSymbolQuote,
)
from app.services import asset_service
from app.services.asset_service import (
    refresh_all_market_prices,
    refresh_market_price_asset,
)


class FakeMarketProvider(MarketPriceProvider):
    """In-memory provider seeded with canned quotes per symbol."""

    name = "fake"

    def __init__(
        self,
        quotes: dict[str, MarketSymbolQuote],
        *,
        rate_limit_after: Optional[int] = None,
        # When set, overrides what `get_latest_prices` returns — lets tests
        # assert that the batch path is hit (or skipped) independently.
        batch_prices: Optional[dict[str, Optional[Decimal]]] = None,
    ) -> None:
        self._quotes = quotes
        self._calls = 0
        self._rate_limit_after = rate_limit_after
        self.batch_calls = 0
        self.quote_calls_after_batch = 0
        self._batch_prices = batch_prices

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        q = query.upper()
        return [
            MarketSymbolMatch(symbol=q, name=f"{q} Inc", exchange="NASDAQ", quote_type="EQUITY"),
        ]

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        self._calls += 1
        if self.batch_calls > 0:
            self.quote_calls_after_batch += 1
        if self._rate_limit_after is not None and self._calls > self._rate_limit_after:
            raise MarketPriceRateLimitedError("simulated")
        return self._quotes.get(symbol.upper())

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        self.batch_calls += 1
        # The batch counts as a "request" for rate-limit purposes — a real
        # yfinance batch is exactly one HTTP call to Yahoo, same surface
        # area as any other request. Bumping `_calls` keeps the counter
        # semantics consistent with get_quote.
        self._calls += 1
        if self._rate_limit_after is not None and self._calls > self._rate_limit_after:
            raise MarketPriceRateLimitedError("simulated batch")
        if self._batch_prices is not None:
            return dict(self._batch_prices)
        out: dict[str, Optional[Decimal]] = {}
        for sym in symbols:
            q = self._quotes.get(sym.upper())
            out[sym.upper()] = Decimal(str(q.price)) if q and q.price else None
        return out


def _quote(symbol: str, price: float, currency: str = "USD") -> MarketSymbolQuote:
    return MarketSymbolQuote(
        symbol=symbol,
        name=f"{symbol} Inc",
        exchange="NASDAQ",
        currency=currency,
        price=price,
        quote_type="EQUITY",
    )


@pytest.mark.asyncio
async def test_create_market_price_asset_seeds_quote_and_initial_value(
    session: AsyncSession, test_user: User, test_workspace
):
    provider = FakeMarketProvider({"AAPL": _quote("AAPL", 180.25)})
    data = AssetCreate(
        name="Apple",
        type="investment",
        currency="USD",  # user-entered, but the quote's currency wins
        valuation_method="market_price",
        ticker="aapl",
        units=Decimal("10"),
    )

    created = await asset_service.create_asset(
        session, test_workspace.id, test_user.id, data, market_provider=provider
    )

    assert created.valuation_method == "market_price"
    # Ticker should be upper-cased and quote currency stamped on the asset.
    assert created.ticker == "AAPL"
    assert created.currency == "USD"
    assert created.last_price == pytest.approx(180.25)
    # Current value = units × last_price, computed live.
    assert created.current_value == pytest.approx(1802.50)
    # One AssetValue should have been seeded from the quote.
    assert created.value_count == 1


@pytest.mark.asyncio
async def test_create_market_price_seeds_opening_buy_at_unit_price(
    session: AsyncSession, test_user: User, test_workspace
):
    """The opening buy uses the user's unit price (preço médio), not the quote."""
    provider = FakeMarketProvider({"AAPL": _quote("AAPL", 200.0)})  # market = 200
    data = AssetCreate(
        name="Apple",
        type="stock",
        valuation_method="market_price",
        ticker="AAPL",
        units=Decimal("10"),
        unit_price=Decimal("150"),  # bought cheaper than today's price
    )
    created = await asset_service.create_asset(
        session, test_workspace.id, test_user.id, data, market_provider=provider
    )
    assert created.average_price == pytest.approx(150.0)   # cost, not the quote
    assert created.total_invested == pytest.approx(1500.0)  # 10 × 150
    assert created.current_value == pytest.approx(2000.0)   # 10 × 200 (live)
    assert created.gain_loss == pytest.approx(500.0)        # unrealized
    assert created.transaction_count == 1


@pytest.mark.asyncio
async def test_create_market_price_without_unit_price_uses_quote(
    session: AsyncSession, test_user: User, test_workspace
):
    """Omitting the unit price falls back to the live quote (bought at market)."""
    provider = FakeMarketProvider({"AAPL": _quote("AAPL", 180.0)})
    data = AssetCreate(
        name="Apple",
        type="stock",
        valuation_method="market_price",
        ticker="AAPL",
        units=Decimal("10"),
    )
    created = await asset_service.create_asset(
        session, test_workspace.id, test_user.id, data, market_provider=provider
    )
    assert created.average_price == pytest.approx(180.0)
    assert created.gain_loss == pytest.approx(0.0)  # cost == current value


@pytest.mark.asyncio
async def test_create_market_price_asset_rejects_missing_ticker(
    session: AsyncSession, test_user: User, test_workspace
):
    provider = FakeMarketProvider({})
    data = AssetCreate(
        name="Bad Entry",
        type="investment",
        valuation_method="market_price",
        units=Decimal("5"),
    )

    with pytest.raises(Exception) as excinfo:  # FastAPI's HTTPException
        await asset_service.create_asset(
            session, test_workspace.id, test_user.id, data, market_provider=provider
        )
    assert "ticker" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_create_market_price_asset_rejects_zero_units(
    session: AsyncSession, test_user: User, test_workspace
):
    provider = FakeMarketProvider({"AAPL": _quote("AAPL", 180.0)})
    data = AssetCreate(
        name="Apple",
        type="investment",
        valuation_method="market_price",
        ticker="AAPL",
        units=Decimal("0"),
    )

    with pytest.raises(Exception) as excinfo:
        await asset_service.create_asset(
            session, test_workspace.id, test_user.id, data, market_provider=provider
        )
    assert "units" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_create_market_price_asset_errors_when_quote_unavailable(
    session: AsyncSession, test_user: User, test_workspace
):
    provider = FakeMarketProvider({})  # no canned quote for any symbol
    data = AssetCreate(
        name="Ghost",
        type="investment",
        valuation_method="market_price",
        ticker="NOPE",
        units=Decimal("1"),
    )

    with pytest.raises(Exception) as excinfo:
        await asset_service.create_asset(
            session, test_workspace.id, test_user.id, data, market_provider=provider
        )
    assert "quote" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_refresh_market_price_updates_cached_price_and_upserts_today(
    session: AsyncSession, test_user: User, test_workspace
):
    # First create with an initial quote, then bump the quote and refresh.
    initial = FakeMarketProvider({"AAPL": _quote("AAPL", 180.00)})
    data = AssetCreate(
        name="Apple",
        type="investment",
        valuation_method="market_price",
        ticker="AAPL",
        units=Decimal("10"),
    )
    created = await asset_service.create_asset(
        session, test_workspace.id, test_user.id, data, market_provider=initial
    )

    # Reload the ORM entity (service returned a schema, not the model).
    asset = await session.get(Asset, created.id)
    assert asset is not None

    # Upstream price moves: refresh should overwrite last_price and amend
    # today's AssetValue in place rather than creating a second row.
    second = FakeMarketProvider({"AAPL": _quote("AAPL", 190.00)})
    updated = await refresh_market_price_asset(session, asset, market_provider=second)
    await session.commit()

    assert updated is True
    assert asset.last_price == Decimal("190.000000")

    values = await session.execute(
        select(AssetValue).where(AssetValue.asset_id == asset.id)
    )
    all_values = list(values.scalars().all())
    todays = [v for v in all_values if v.date == date.today()]
    # Exactly one value for today — the refresh must upsert, not append.
    assert len(todays) == 1
    assert todays[0].amount == Decimal("1900.000000")
    assert todays[0].source == "sync"


@pytest.mark.asyncio
async def test_refresh_all_uses_batch_endpoint(
    session: AsyncSession, test_user: User, test_workspace
):
    """Scheduled refresh should hit the batch path once, not N single quotes."""
    provider = FakeMarketProvider(
        {
            "AAPL": _quote("AAPL", 180.0),
            "MSFT": _quote("MSFT", 400.0),
            "GOOG": _quote("GOOG", 150.0),
        }
    )
    for ticker in ("AAPL", "MSFT", "GOOG"):
        await asset_service.create_asset(
            session,
            test_workspace.id,
            test_user.id,
            AssetCreate(
                name=ticker,
                type="investment",
                valuation_method="market_price",
                ticker=ticker,
                units=Decimal("1"),
            ),
            market_provider=provider,
        )

    # Reset counters — create_asset already used get_quote 3 times.
    provider.batch_calls = 0
    provider.quote_calls_after_batch = 0

    result = await refresh_all_market_prices(session, market_provider=provider)

    assert result == {"refreshed": 3, "skipped": 0, "rate_limited": 0}
    # One batch call covered all three symbols.
    assert provider.batch_calls == 1
    # No per-symbol fallback should have fired since the batch returned everything.
    assert provider.quote_calls_after_batch == 0


@pytest.mark.asyncio
async def test_refresh_all_falls_back_when_batch_misses_symbol(
    session: AsyncSession, test_user: User, test_workspace
):
    """If a symbol is missing from the batch response, per-asset path picks it up."""
    provider = FakeMarketProvider(
        {"AAPL": _quote("AAPL", 180.0), "MSFT": _quote("MSFT", 400.0)}
    )
    for ticker in ("AAPL", "MSFT"):
        await asset_service.create_asset(
            session,
            test_workspace.id,
            test_user.id,
            AssetCreate(
                name=ticker,
                type="investment",
                valuation_method="market_price",
                ticker=ticker,
                units=Decimal("1"),
            ),
            market_provider=provider,
        )

    # Simulate Yahoo returning only AAPL from the batch — MSFT should
    # drop through to the per-asset fallback.
    provider = FakeMarketProvider(
        {"AAPL": _quote("AAPL", 181.0), "MSFT": _quote("MSFT", 402.0)},
        batch_prices={"AAPL": Decimal("181.0"), "MSFT": None},
    )

    result = await refresh_all_market_prices(session, market_provider=provider)
    assert result["refreshed"] == 2
    assert provider.batch_calls == 1
    # Exactly one per-asset fallback call (for MSFT).
    assert provider.quote_calls_after_batch == 1


@pytest.mark.asyncio
async def test_refresh_all_halts_on_rate_limit(
    session: AsyncSession, test_user: User, test_workspace
):
    # Create two market-priced assets
    provider = FakeMarketProvider({"AAPL": _quote("AAPL", 180.0), "MSFT": _quote("MSFT", 400.0)})
    for ticker in ("AAPL", "MSFT"):
        await asset_service.create_asset(
            session,
            test_workspace.id,
            test_user.id,
            AssetCreate(
                name=ticker,
                type="investment",
                valuation_method="market_price",
                ticker=ticker,
                units=Decimal("1"),
            ),
            market_provider=provider,
        )

    # Now make any further quote call rate-limit — refresh should stop,
    # not continue hitting Yahoo after the first throttled response. Since
    # the batch path is tried first and our fake's batch helper internally
    # calls get_quote (no canned batch_prices), the rate-limit fires at
    # the batch step, which halts the whole cycle.
    limited = FakeMarketProvider(
        {"AAPL": _quote("AAPL", 181.0), "MSFT": _quote("MSFT", 401.0)},
        rate_limit_after=0,
    )
    result = await refresh_all_market_prices(session, market_provider=limited)

    assert result["rate_limited"] == 1
    # Nothing refreshed — batch halt means every asset landed in the
    # skipped bucket, no per-asset fallback was attempted.
    assert result["refreshed"] == 0
    assert result["skipped"] == 2
