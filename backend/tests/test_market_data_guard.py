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
    LatestClose,
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


def test_a_search_yahoo_could_not_decode_is_a_failure_not_no_matches(fake_redis):
    """yfinance hides a decode error and leaves the response empty.

    That empty list used to be cached as "no matches" for an hour, for every
    workspace, so a malformed answer from Yahoo looked like a wrong ticker.
    """

    class _Swallowed:
        quotes: list = []
        response: dict = {}

    with patch("yfinance.Search", return_value=_Swallowed()):
        assert YFinanceProvider._search_sync("apple", 5) is None

    class _Empty:
        quotes: list = []
        response = {"quotes": [], "news": [], "lists": []}

    with patch("yfinance.Search", return_value=_Empty()):
        assert YFinanceProvider._search_sync("zzzz", 5) == []


@pytest.mark.asyncio
async def test_a_quote_yahoo_answered_without_a_price_is_not_cached(fake_redis, yahoo):
    """A price that came back empty must not pin a 404 on the symbol for 15 minutes."""

    def no_price(symbol: str):
        yahoo["price"] += 1
        return {"currency": "USD", "exchange": "NMS", "price": None, "quote_type": "EQUITY"}

    provider = YFinanceProvider()
    with patch.object(YFinanceProvider, "_price_sync", staticmethod(no_price)):
        assert await provider.get_quote("AAPL") is None
        assert await provider.get_quote("AAPL") is None
    assert "market:yahoo:price:AAPL" not in fake_redis.data
    # Not cached, so the ticker is asked again once Yahoo has an answer.
    assert yahoo["price"] == 2


@pytest.mark.asyncio
async def test_a_batch_with_nothing_for_any_ticker_probes_yahoo_and_backs_off(
    fake_redis, yahoo
):
    """A rate limit inside `yf.download` is invisible; the probe surfaces it."""

    def refused(symbol: str):
        yahoo["price"] += 1
        raise MarketPriceRateLimitedError("429")

    def empty(symbols: list[str]):
        return {s: None for s in symbols}

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(empty)),
        patch.object(YFinanceProvider, "_price_sync", staticmethod(refused)),
    ):
        with pytest.raises(MarketPriceRateLimitedError):
            await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    assert yahoo["price"] == 1
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_batch_with_a_delisted_ticker_does_not_back_off(fake_redis, yahoo):
    """Yahoo still answers, it just has nothing for that ticker: no cooldown."""

    def empty(symbols: list[str]):
        return {s: None for s in symbols}

    with patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(empty)):
        closes = await YFinanceProvider().get_latest_closes(["GONE"])

    assert closes == {"GONE": None}
    # The probe ran and Yahoo answered (with nothing): not a rate limit.
    assert yahoo["price"] == 1
    assert "market:yahoo:cooldown" not in fake_redis.data


@pytest.mark.asyncio
async def test_a_rate_limit_downloading_the_batch_starts_the_cooldown(fake_redis):
    """When `yf.download` does raise it, the cooldown starts and the caller is told."""
    from yfinance.exceptions import YFRateLimitError

    def refused(symbols: list[str]):
        raise YFRateLimitError()

    with patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(refused)):
        with pytest.raises(MarketPriceRateLimitedError):
            await YFinanceProvider().get_latest_closes(["AAPL"])

    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_rate_limit_mid_batch_keeps_the_chunks_already_priced(fake_redis, yahoo):
    """Losing the last chunk must not throw away the closes the first ones had."""
    from datetime import date
    from decimal import Decimal

    from yfinance.exceptions import YFRateLimitError

    def download(symbols: list[str]):
        if symbols == ["AAPL"]:
            return {"AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))}
        raise YFRateLimitError()

    with (
        patch.object(YFinanceProvider, "_BATCH_CHUNK_SIZE", 1),
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    # The priced chunk survives, the one that never answered stays None so the
    # caller falls back per holding (where the cooldown halts it).
    assert closes["AAPL"] is not None
    assert closes["AAPL"].price == Decimal("190.5")
    assert closes["MSFT"] is None
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_rate_limit_beside_priced_tickers_starts_the_cooldown(fake_redis, yahoo):
    """One ticker priced must not hide the 429 `yf.download` swallowed for another."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        return {
            "AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)),
            "MSFT": None,
        }

    def refused(symbol: str):
        yahoo["price"] += 1
        raise MarketPriceRateLimitedError("429")

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(YFinanceProvider, "_price_sync", staticmethod(refused)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    # The priced ticker survives and the unanswered one is probed, so the
    # partial batch starts the cooldown instead of looking like a quiet sweep.
    assert closes["AAPL"].price == Decimal("190.5")
    assert closes["MSFT"] is None
    assert yahoo["price"] == 1
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_denied_probe_keeps_the_chunks_already_priced(fake_redis, yahoo):
    """A probe our own throttle denies must not erase closes a chunk returned."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        if symbols == ["AAPL"]:
            return {"AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))}
        return {s: None for s in symbols}

    with (
        patch.object(YFinanceProvider, "_BATCH_CHUNK_SIZE", 1),
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(market_data_guard, "take_call_slot", return_value=False),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "GONE"])

    assert closes["AAPL"].price == Decimal("190.5")
    assert closes["GONE"] is None
    assert yahoo["price"] == 0
    # Our own cap is not Yahoo refusing: no cooldown is started for it.
    assert "market:yahoo:cooldown" not in fake_redis.data


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
