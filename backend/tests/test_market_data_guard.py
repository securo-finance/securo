"""The shared market-data cache and the instance-wide throttle in front of Yahoo.

All workspaces of an instance reach Yahoo from one address, so these check
that a repeated ticker costs one upstream call whoever asks, that a 429 is
answered as "try again" instead of a 500 and stops further calls, and that
cached answers keep working while Yahoo refuses.
"""

from __future__ import annotations

import logging
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
        return {s: None for s in symbols}, False

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
        return {s: None for s in symbols}, False

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
            return {"AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))}, False
        raise YFRateLimitError()

    with (
        patch.object(YFinanceProvider, "_BATCH_CHUNK_SIZE", 1),
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    # The priced chunk survives, the one that never answered stays None so the
    # caller falls back per holding (where the cooldown halts it).
    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
    assert closes["MSFT"] is None
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_rate_limit_beside_priced_tickers_starts_the_cooldown(fake_redis, yahoo):
    """One ticker priced must not hide the 429 `yf.download` swallowed for another."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        return (
            {
                "AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)),
                "MSFT": None,
            },
            False,
        )

    def refused(symbol: str):
        # Probing the priced ticker would hide the refusal, so pin the symbol.
        assert symbol == "MSFT"
        yahoo["price"] += 1
        raise MarketPriceRateLimitedError("429")

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(YFinanceProvider, "_price_sync", staticmethod(refused)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    # The priced ticker survives and the unanswered one is probed, so the
    # partial batch starts the cooldown instead of looking like a quiet sweep.
    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
    assert closes["MSFT"] is None
    assert yahoo["price"] == 1
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_rate_limit_the_batch_reported_starts_the_cooldown_without_a_probe(
    fake_redis, yahoo
):
    """A 429 the batch itself recorded counts even when a probe would answer."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        return (
            {
                "AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)),
                "MSFT": None,
            },
            # yf.download swallowed a 429 for MSFT, which the frame cannot say.
            True,
        )

    def price(symbol: str):
        yahoo["price"] += 1
        return None

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(YFinanceProvider, "_price_sync", staticmethod(price)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])

    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
    assert closes["MSFT"] is None
    # The batch already reported the refusal: no probe that could answer back.
    assert yahoo["price"] == 0
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_batch_that_reported_a_rate_limit_skips_the_currency_lookup(
    fake_redis, yahoo
):
    """The minor-unit lookup reaches Yahoo unthrottled, so a 429 must stop it.

    ``_get_currencies_sync`` calls ``yf.Ticker(...).fast_info`` directly, with
    no ``take_call_slot`` and no cooldown, and it runs on every priced *.L and
    *.JO ticker of the batch. Letting it run after the batch reported a 429
    would send up to a hundred uncooled requests. Those closes cannot be
    scaled without their currency either, so they come back None and fall
    back to ``get_quote``, which the cooldown blocks.
    """
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        return (
            {
                "EQQQ.L": LatestClose(price=Decimal("38450"), on=date(2026, 11, 25)),
                "AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)),
            },
            # yf.download swallowed a 429 for a ticker of this batch.
            True,
        )

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(YFinanceProvider, "_get_currencies_sync") as currencies,
    ):
        closes = await YFinanceProvider().get_latest_closes(["EQQQ.L", "AAPL"])

    currencies.assert_not_called()
    assert closes["EQQQ.L"] is None
    # The ticker that needed no currency keeps the close the batch did price.
    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


def test_yf_download_rate_limits_are_read_from_the_errors_it_logs():
    """The failures ``yf.download`` keeps to itself only surface in its log."""
    yf_logger = logging.getLogger("yfinance")

    def rate_limited(tickers, **kwargs):
        yf_logger.error(
            "1 Failed download:\n['MSFT']: YFRateLimitError('Too Many Requests. "
            "Rate limited. Try after a while.')"
        )
        return None

    def delisted(tickers, **kwargs):
        yf_logger.error(
            "1 Failed download:\n['GONE']: YFPricesMissingError('$GONE: possibly "
            "delisted; no price data found')"
        )
        return None

    with patch("yfinance.download", rate_limited):
        _, refused = YFinanceProvider._download_closes_sync(["AAPL", "MSFT"])
    assert refused is True

    with patch("yfinance.download", delisted):
        _, refused = YFinanceProvider._download_closes_sync(["GONE"])
    # Yahoo answered, it just has nothing for a delisted ticker.
    assert refused is False


@pytest.mark.asyncio
async def test_a_delisted_ticker_does_not_hide_a_rate_limit_behind_it(fake_redis, yahoo):
    """The probe has to cover every unanswered ticker, not only the first."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        return (
            {
                "AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)),
                "GONE": None,
                "MSFT": None,
            },
            False,
        )

    probed: list[str] = []

    def price(symbol: str):
        probed.append(symbol)
        yahoo["price"] += 1
        if symbol == "MSFT":
            raise MarketPriceRateLimitedError("429")
        # GONE: Yahoo answered, it just has nothing for a delisted ticker.
        return None

    with (
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        patch.object(YFinanceProvider, "_price_sync", staticmethod(price)),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "GONE", "MSFT"])

    # GONE answers (no 429), so the probe must go on to MSFT, whose refusal
    # `yf.download` swallowed. Stopping at the first unanswered ticker would
    # leave the shared cooldown unset.
    assert probed == ["GONE", "MSFT"]
    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
    assert closes["GONE"] is None
    assert closes["MSFT"] is None
    assert fake_redis.data.get("market:yahoo:cooldown") == "1"


@pytest.mark.asyncio
async def test_a_denied_probe_keeps_the_chunks_already_priced(fake_redis, yahoo):
    """A probe our own throttle denies must not erase closes a chunk returned."""
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        if symbols == ["AAPL"]:
            return {"AAPL": LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))}, False
        return {s: None for s in symbols}, False

    with (
        patch.object(YFinanceProvider, "_BATCH_CHUNK_SIZE", 1),
        patch.object(YFinanceProvider, "_download_closes_sync", staticmethod(download)),
        # Both chunks get their slot; only the probe after them is denied.
        patch.object(market_data_guard, "take_call_slot", side_effect=[True, True, False]),
    ):
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "GONE"])

    priced = closes["AAPL"]
    assert priced is not None
    assert priced.price == Decimal("190.5")
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


def test_an_info_with_no_usable_fields_is_a_failure_not_metadata():
    """yfinance hides a failed quoteSummary as a dict of nulls; do not cache it."""

    class _Ticker:
        def __init__(self, symbol: str) -> None:
            self.info = {"trailingPegRatio": None}

    with patch("yfinance.Ticker", _Ticker):
        assert YFinanceProvider._info_sync("AAPL") is None


def test_malformed_metadata_keeps_the_usable_fields():
    """A wrongly typed field in `info` must not raise out of the display lookup."""

    class _Ticker:
        def __init__(self, symbol: str) -> None:
            self.info = {"longName": "Apple Inc.", "quoteType": 123, "website": 42}

    with patch("yfinance.Ticker", _Ticker):
        info = YFinanceProvider._info_sync("AAPL")

    assert info == {"name": "Apple Inc.", "quote_type": None, "website": None}


def test_a_bad_long_name_does_not_hide_a_usable_short_name():
    """A wrongly typed `longName` must fall through to `shortName`, not to None."""

    class _Ticker:
        def __init__(self, symbol: str) -> None:
            self.info = {"longName": 123, "shortName": "Apple", "quoteType": "EQUITY"}

    with patch("yfinance.Ticker", _Ticker):
        info = YFinanceProvider._info_sync("AAPL")

    assert info == {"name": "Apple", "quote_type": "EQUITY", "website": None}


def _closes_by_call(downloaded: list[list[str]]):
    from datetime import date
    from decimal import Decimal

    def download(symbols: list[str]):
        downloaded.append(list(symbols))
        return {s: LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25)) for s in symbols}, False

    return download


@pytest.mark.asyncio
async def test_workspaces_sharing_a_ticker_download_its_close_once(fake_redis, yahoo):
    """The daily sweep asks once per workspace; the close is fetched only by the first."""
    from datetime import date
    from decimal import Decimal

    downloaded: list[list[str]] = []
    with patch.object(
        YFinanceProvider, "_download_closes_sync", staticmethod(_closes_by_call(downloaded))
    ):
        answers = [
            await YFinanceProvider().get_latest_closes(tickers)
            for tickers in (["AAPL"], ["aapl", "MSFT"], ["AAPL", "MSFT"])
        ]

    # Only the tickers no workspace fetched yet reach Yahoo.
    assert downloaded == [["AAPL"], ["MSFT"]]
    close = LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))
    assert answers[2] == {"AAPL": close, "MSFT": close}
    assert yahoo["price"] == 0


@pytest.mark.asyncio
async def test_a_batch_during_the_cooldown_answers_from_the_cache_only(fake_redis, yahoo):
    from datetime import date
    from decimal import Decimal

    downloaded: list[list[str]] = []
    with patch.object(
        YFinanceProvider, "_download_closes_sync", staticmethod(_closes_by_call(downloaded))
    ):
        await YFinanceProvider().get_latest_closes(["AAPL"])
        await market_data_guard.start_cooldown()
        closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])
        with pytest.raises(MarketPriceRateLimitedError):
            await YFinanceProvider().get_latest_closes(["MSFT"])

    assert downloaded == [["AAPL"]]
    assert closes["AAPL"] == LatestClose(price=Decimal("190.5"), on=date(2026, 11, 25))
    assert closes["MSFT"] is None
    assert yahoo["price"] == 0


def test_an_unreadable_cached_close_is_a_miss():
    from app.providers.market_price import _close_from_cache

    for raw in (None, [], {}, {"price": "abc"}, {"price": "NaN"}, {"price": "1", "on": "x"}):
        assert _close_from_cache(raw) is None


@pytest.mark.asyncio
async def test_a_rate_limit_in_one_workspace_does_not_end_the_sweep():
    """The sweep goes on: later workspaces still get what the cache holds."""
    from contextlib import asynccontextmanager

    from app.tasks import asset_tasks

    class _Engine:
        async def dispose(self):
            pass

    @asynccontextmanager
    async def _session():
        yield object()

    @asynccontextmanager
    async def _tz(_session, fresh=False):
        yield

    async def makers(_maker):
        return [_session, _session, _session]

    answers = iter([
        {"refreshed": 1, "skipped": 1, "rate_limited": 1},
        {"refreshed": 2, "skipped": 0, "rate_limited": 0},
        {"refreshed": 0, "skipped": 1, "rate_limited": 1},
    ])
    swept: list[object] = []

    async def refresh(session):
        swept.append(session)
        return next(answers)

    with (
        patch.object(asset_tasks, "_make_session_maker", return_value=(_Engine(), None)),
        patch.object(asset_tasks, "workspace_makers", makers),
        patch.object(asset_tasks, "use_timezone", _tz),
        patch.object(asset_tasks, "refresh_all_market_prices", refresh),
    ):
        total = await asset_tasks._refresh_market_prices()

    assert len(swept) == 3
    assert total == {"refreshed": 3, "skipped": 2, "rate_limited": 2}


@pytest.mark.asyncio
async def test_ten_workspaces_holding_the_same_ten_tickers_cost_ten_calls(fake_redis, yahoo):
    """The sweep asks once per workspace; Yahoo sees each ticker once."""
    tickers = [f"T{i}" for i in range(10)]
    downloaded: list[list[str]] = []
    with patch.object(
        YFinanceProvider, "_download_closes_sync", staticmethod(_closes_by_call(downloaded))
    ):
        for _workspace in range(10):
            closes = await YFinanceProvider().get_latest_closes(tickers)
            assert all(closes[t] is not None for t in tickers)

    assert sum(len(batch) for batch in downloaded) == 10
    assert yahoo["price"] == 0


@pytest.mark.asyncio
async def test_a_batch_waits_its_turn_in_the_call_throttle(fake_redis, yahoo):
    """No free call slot: the batch answers from the cache and downloads nothing."""
    downloaded: list[list[str]] = []

    async def no_slot() -> bool:
        return False

    with patch.object(
        YFinanceProvider, "_download_closes_sync", staticmethod(_closes_by_call(downloaded))
    ):
        await YFinanceProvider().get_latest_closes(["AAPL"])
        with patch.object(market_data_guard, "take_call_slot", no_slot):
            closes = await YFinanceProvider().get_latest_closes(["AAPL", "MSFT"])
            with pytest.raises(MarketPriceRateLimitedError):
                await YFinanceProvider().get_latest_closes(["MSFT"])

    assert downloaded == [["AAPL"]]
    assert closes["AAPL"] is not None
    assert closes["MSFT"] is None


def test_a_single_ticker_batch_reads_its_close_from_the_ticker_level():
    """yfinance keeps the (ticker, field) columns for one ticker too."""
    from datetime import date
    from decimal import Decimal

    import pandas as pd

    frame = pd.DataFrame(
        [[410.25], [412.5]],
        index=pd.to_datetime(["2026-11-24", "2026-11-25"]),
        columns=pd.MultiIndex.from_tuples([("MSFT", "Close")]),
    )

    with patch("yfinance.download", return_value=frame):
        closes, refused = YFinanceProvider._download_closes_sync(["MSFT"])

    assert refused is False
    assert closes["MSFT"] is not None
    assert closes["MSFT"].price == Decimal("412.5")
    assert closes["MSFT"].on == date(2026, 11, 25)
