"""The shared market-data cache and the instance-wide throttle in front of Yahoo.

All workspaces of an instance reach Yahoo from one address, so these check
that a repeated ticker costs one upstream call whoever asks, that a 429 is
answered as "try again" instead of a 500 and stops further calls, and that
cached answers keep working while Yahoo refuses.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.providers import market_data_guard
from app.providers.market_price import (
    CompositeMarketPriceProvider,
    MarketPriceRateLimitedError,
    YFinanceProvider,
    set_market_price_provider,
)


class _Pipe:
    def __init__(self, store: "_FakeRedis") -> None:
        self._store = store
        self._ops: list[tuple[str, str]] = []

    def incr(self, key: str) -> "_Pipe":
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, _seconds: int) -> "_Pipe":
        self._ops.append(("expire", key))
        return self

    async def execute(self) -> list:
        out: list = []
        for op, key in self._ops:
            if op == "incr":
                self._store.data[key] = str(int(self._store.data.get(key, "0")) + 1)
                out.append(int(self._store.data[key]))
            else:
                out.append(True)
        return out


class _FakeRedis:
    """Just the commands the guard uses, kept in a dict (TTL ignored)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def pipeline(self) -> _Pipe:
        return _Pipe(self)


@pytest.fixture
def fake_redis():
    store = _FakeRedis()

    async def _get():
        return store

    with patch("app.core.redis.get_redis", _get):
        yield store


@pytest.fixture
def yahoo():
    """A real YFinanceProvider whose network halves are counted stubs."""
    calls = {"price": 0, "info": 0, "search": 0}

    def price(symbol: str):
        calls["price"] += 1
        return {"currency": "USD", "exchange": "NMS", "price": 190.5, "quote_type": "EQUITY"}

    def info(symbol: str):
        calls["info"] += 1
        return {"name": "Apple Inc.", "quote_type": "EQUITY", "website": "https://www.apple.com"}

    def search(query: str, limit: int):
        calls["search"] += 1
        return [{"symbol": "AAPL", "longname": "Apple Inc.", "exchDisp": "NASDAQ", "quoteType": "equity"}]

    with (
        patch.object(YFinanceProvider, "_price_sync", staticmethod(price)),
        patch.object(YFinanceProvider, "_info_sync", staticmethod(info)),
        patch.object(YFinanceProvider, "_search_sync", staticmethod(search)),
    ):
        set_market_price_provider(CompositeMarketPriceProvider(YFinanceProvider()))
        yield calls
        set_market_price_provider(None)


@pytest.mark.asyncio
async def test_same_ticker_from_two_workspaces_costs_one_upstream_call(
    client, auth_headers, fake_redis, yahoo
):
    other = await client.post(
        "/api/workspaces", json={"name": "Second workspace"}, headers=auth_headers
    )
    assert other.status_code in (200, 201), other.text
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}

    first = await client.get("/api/assets/market/quote?symbol=aapl", headers=auth_headers)
    second = await client.get("/api/assets/market/quote?symbol=AAPL", headers=other_headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["name"] == "Apple Inc."
    assert yahoo["price"] == 1
    assert yahoo["info"] == 1
    # The key is the public symbol alone: nothing of the workspace or user.
    assert set(fake_redis.data) >= {"market:yahoo:price:AAPL", "market:yahoo:info:AAPL"}


@pytest.mark.asyncio
async def test_search_is_cached_and_a_failed_search_is_not(fake_redis, yahoo):
    provider = YFinanceProvider()
    assert [m.symbol for m in await provider.search("apple", limit=5)] == ["AAPL"]
    assert [m.symbol for m in await provider.search("Apple", limit=5)] == ["AAPL"]
    assert yahoo["search"] == 1

    with patch.object(YFinanceProvider, "_search_sync", staticmethod(lambda q, n: None)):
        assert await provider.search("msft", limit=5) == []
    assert "market:yahoo:search:5:msft" not in fake_redis.data


@pytest.mark.asyncio
async def test_yahoo_429_is_a_429_and_cached_tickers_keep_working(
    client, auth_headers, fake_redis, yahoo
):
    warm = await client.get("/api/assets/market/quote?symbol=AAPL", headers=auth_headers)
    assert warm.status_code == 200

    def refused(symbol: str):
        yahoo["price"] += 1
        raise MarketPriceRateLimitedError("429")

    with patch.object(YFinanceProvider, "_price_sync", staticmethod(refused)):
        cold = await client.get("/api/assets/market/quote?symbol=MSFT", headers=auth_headers)
        assert cold.status_code == 429
        assert "Try again" in cold.json()["detail"]
        calls_after_429 = yahoo["price"]

        # Yahoo is cooling down: nobody calls it again for now...
        again = await client.get("/api/assets/market/quote?symbol=NVDA", headers=auth_headers)
        assert again.status_code == 429
        assert yahoo["price"] == calls_after_429

        # ...and what is already cached is still served.
        cached = await client.get("/api/assets/market/quote?symbol=AAPL", headers=auth_headers)
        assert cached.status_code == 200
        assert cached.json()["price"] == 190.5


@pytest.mark.asyncio
async def test_throttle_caps_calls_per_second_for_the_instance(fake_redis):
    with (
        patch.object(market_data_guard, "MAX_WAIT_SECONDS", 0),
        patch("app.providers.market_data_guard.time.time", return_value=1_000_000.2),
    ):
        taken = [await market_data_guard.take_call_slot() for _ in range(6)]
    allowed = market_data_guard.MAX_CALLS_PER_SECOND
    assert allowed < 5
    assert taken == [True] * allowed + [False] * (6 - allowed)


@pytest.mark.asyncio
async def test_throttled_quote_answers_429_without_calling_yahoo(
    client, auth_headers, fake_redis, yahoo
):
    with patch.object(market_data_guard, "take_call_slot", return_value=False):
        resp = await client.get("/api/assets/market/quote?symbol=AAPL", headers=auth_headers)
    assert resp.status_code == 429
    assert yahoo["price"] == 0


@pytest.mark.asyncio
async def test_redis_down_lets_lookups_through(yahoo):
    async def _down():
        raise ConnectionError("redis is down")

    with patch("app.core.redis.get_redis", _down):
        quote = await YFinanceProvider().get_quote("AAPL")
    assert quote is not None and quote.price == 190.5
