from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

import httpx
import pytest

from app.providers.benchmark import (
    B3DIBenchmarkProvider,
    BenchmarkHistoryPoint,
    BenchmarkProvider,
    BenchmarkProviderError,
    BenchmarkRateLimitedError,
    DI_B3_SYMBOL,
    CompositeBenchmarkProvider,
    DailyRateHistoryLoader,
    DailyRateSource,
    YahooBenchmarkProvider,
    _BoundedTTLCache,
    accumulate_daily_rates,
)
from app.providers.market_price import MarketPriceProvider
from app.schemas.asset import BenchmarkMatch, MarketSymbolMatch, MarketSymbolQuote


class FakeMarketProvider(MarketPriceProvider):
    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return [
            MarketSymbolMatch(symbol="ACME", name="Acme", exchange="NYSE", quote_type="EQUITY"),
            MarketSymbolMatch(
                symbol="^ACME", name="Acme Index", exchange="SNP", quote_type="INDEX"
            ),
            MarketSymbolMatch(
                symbol="ACME-ETF", name="Acme ETF", exchange="NYSE", quote_type="ETF"
            ),
        ]

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        return None


class CountingProvider(BenchmarkProvider):
    def __init__(self, name: str, match: BenchmarkMatch) -> None:
        self.name = name
        self.match = match
        self.search_calls = 0
        self.history_calls = 0

    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        self.search_calls += 1
        return [self.match]

    async def history(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        self.history_calls += 1
        return [BenchmarkHistoryPoint(start_date, 100.0), BenchmarkHistoryPoint(end_date, 110.0)]


class FailingProvider(BenchmarkProvider):
    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        raise BenchmarkProviderError("upstream failed")

    async def history(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        raise BenchmarkProviderError("upstream failed")


@pytest.fixture
def every_symbol_charted(monkeypatch):
    monkeypatch.setattr(
        YahooBenchmarkProvider,
        "_symbols_with_history_sync",
        staticmethod(lambda symbols: set(symbols)),
    )


@pytest.mark.asyncio
async def test_yahoo_search_keeps_every_priced_instrument_type(every_symbol_charted):
    class MixedMarketProvider(FakeMarketProvider):
        async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
            return [
                *await super().search(query, limit),
                MarketSymbolMatch(
                    symbol="BTC-USD", name="Bitcoin USD", quote_type="CRYPTOCURRENCY"
                ),
                MarketSymbolMatch(symbol="ACME=F", name="Acme Futures", quote_type="FUTURE"),
                MarketSymbolMatch(symbol="ACME260619C1", name="Acme Call", quote_type="OPTION"),
            ]

    provider = YahooBenchmarkProvider(MixedMarketProvider())
    results = await provider.search("acme")
    assert [(item.symbol, item.provider) for item in results] == [
        ("ACME", "yahoo"),
        ("^ACME", "yahoo"),
        ("ACME-ETF", "yahoo"),
        ("BTC-USD", "yahoo"),
    ]


@pytest.mark.asyncio
async def test_yahoo_search_drops_symbols_without_history(monkeypatch):
    checked: list[list[str]] = []

    def symbols_with_history(symbols: list[str]) -> set[str]:
        checked.append(symbols)
        return {"ACME", "ACME-ETF"}

    monkeypatch.setattr(
        YahooBenchmarkProvider, "_symbols_with_history_sync", staticmethod(symbols_with_history)
    )
    results = await YahooBenchmarkProvider(FakeMarketProvider()).search("acme")
    assert [item.symbol for item in results] == ["ACME", "ACME-ETF"]
    assert checked == [["ACME", "^ACME", "ACME-ETF"]]


def test_yahoo_history_check_runs_in_batches_until_the_limit_is_filled(monkeypatch):
    candidates = [
        BenchmarkMatch(symbol=f"S{index}", name=f"S{index}", provider="yahoo")
        for index in range(45)
    ]
    batches: list[list[str]] = []

    def symbols_with_history(symbols: list[str]) -> set[str]:
        batches.append(symbols)
        # Only every other symbol has a history.
        return {symbol for symbol in symbols if int(symbol[1:]) % 2 == 0}

    monkeypatch.setattr(
        YahooBenchmarkProvider, "_symbols_with_history_sync", staticmethod(symbols_with_history)
    )
    kept = YahooBenchmarkProvider._keep_charted_sync(candidates, 15)
    assert [item.symbol for item in kept] == [f"S{index}" for index in range(0, 30, 2)]
    assert [len(batch) for batch in batches] == [20, 20]


def test_yahoo_history_check_failure_keeps_results_unchecked(monkeypatch):
    def failing_check(symbols: list[str]) -> set[str]:
        raise BenchmarkProviderError("spark unavailable")

    monkeypatch.setattr(
        YahooBenchmarkProvider, "_symbols_with_history_sync", staticmethod(failing_check)
    )
    candidates = [BenchmarkMatch(symbol="^GSPC", name="S&P 500", provider="yahoo")]
    assert YahooBenchmarkProvider._keep_charted_sync(candidates, 15) == candidates


@pytest.mark.asyncio
async def test_yahoo_history_check_rate_limit_from_yfinance_fails_the_search(monkeypatch):
    from yfinance.exceptions import YFRateLimitError

    def rate_limited(symbols: list[str]) -> set[str]:
        # yfinance raises this itself when Yahoo keeps answering 429.
        raise YFRateLimitError()

    monkeypatch.setattr(
        YahooBenchmarkProvider, "_symbols_with_history_sync", staticmethod(rate_limited)
    )
    with pytest.raises(BenchmarkRateLimitedError):
        await YahooBenchmarkProvider(FakeMarketProvider()).search("acme")


@pytest.mark.asyncio
async def test_yahoo_history_check_rate_limit_fails_the_search(monkeypatch):
    def rate_limited(symbols: list[str]) -> set[str]:
        raise BenchmarkRateLimitedError("slow down")

    monkeypatch.setattr(
        YahooBenchmarkProvider, "_symbols_with_history_sync", staticmethod(rate_limited)
    )
    with pytest.raises(BenchmarkRateLimitedError):
        await YahooBenchmarkProvider(FakeMarketProvider()).search("acme")


def test_yahoo_history_check_needs_two_closes(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            # Shape of Yahoo's spark response: an index quoted without
            # history returns one point, an unknown symbol is left out.
            return {
                "^GSPC": {"symbol": "^GSPC", "timestamp": [1, 2, 3], "close": [1.0, 2.0, 3.0]},
                "^SPBTCFU": {"symbol": "^SPBTCFU", "timestamp": [1], "close": [563.9]},
            }

    class FakeYfData:
        def get(self, url, params, timeout):
            assert params["symbols"] == "^GSPC,^SPBTCFU,NOPE"
            assert params["interval"] == "1d"
            return FakeResponse()

    monkeypatch.setattr("yfinance.data.YfData", FakeYfData)
    assert YahooBenchmarkProvider._symbols_with_history_sync(["^GSPC", "^SPBTCFU", "NOPE"]) == {
        "^GSPC"
    }


def fake_spark(monkeypatch, payload):
    class FakeResponse:
        status_code = 200

        def json(self):
            return payload

    class FakeYfData:
        def get(self, url, params, timeout):
            return FakeResponse()

    monkeypatch.setattr("yfinance.data.YfData", FakeYfData)


def test_yahoo_history_check_accepts_only_unknown_symbols(monkeypatch):
    # Every symbol unknown: Yahoo leaves them all out of an empty payload.
    fake_spark(monkeypatch, {})
    assert YahooBenchmarkProvider._symbols_with_history_sync(["NOPE", "NADA"]) == set()


def test_yahoo_history_check_in_an_unknown_format_keeps_results_unchecked(monkeypatch):
    # A 200 keyed by none of the requested symbols means the format changed.
    fake_spark(monkeypatch, {"spark": {"result": [], "error": None}})
    with pytest.raises(BenchmarkProviderError):
        YahooBenchmarkProvider._symbols_with_history_sync(["^GSPC", "IBIT"])

    candidates = [
        BenchmarkMatch(symbol="^GSPC", name="S&P 500", provider="yahoo"),
        BenchmarkMatch(symbol="IBIT", name="iShares Bitcoin Trust ETF", provider="yahoo"),
    ]
    assert YahooBenchmarkProvider._keep_charted_sync(candidates, 15) == candidates


def test_yahoo_lookup_parses_priced_instrument_documents(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "finance": {
                    "result": [
                        {
                            "documents": [
                                {
                                    "symbol": "^GSPC",
                                    "shortName": "S&P 500",
                                    "exchange": "SNP",
                                    "quoteType": "index",
                                },
                                {
                                    "symbol": "SPY",
                                    "shortName": "SPDR S&P 500 ETF",
                                    "exchange": "PCX",
                                    "quoteType": "etf",
                                },
                                {
                                    "symbol": "ES=F",
                                    "shortName": "E-Mini S&P 500",
                                    "exchange": "CME",
                                    "quoteType": "future",
                                },
                            ]
                        }
                    ]
                }
            }

    class FakeYfData:
        def get(self, url, params, timeout):
            assert params["query"] == "500"
            assert params["type"] == "all"
            return FakeResponse()

    monkeypatch.setattr("yfinance.data.YfData", FakeYfData)
    results = YahooBenchmarkProvider._lookup_sync("500", 15)
    assert results == [
        BenchmarkMatch(symbol="^GSPC", name="S&P 500", exchange="SNP", provider="yahoo"),
        BenchmarkMatch(symbol="SPY", name="SPDR S&P 500 ETF", exchange="PCX", provider="yahoo"),
    ]


@pytest.mark.asyncio
async def test_di_is_discoverable_from_provider_catalog():
    provider = B3DIBenchmarkProvider()
    assert (await provider.search("CDI"))[0].symbol == "B3:DI"
    assert (await provider.search("indice di b3"))[0].provider == "b3"
    assert await provider.search("Nasdaq") == []
    assert await provider.search("finance") == []


def test_daily_percentage_rates_are_compounded():
    points = accumulate_daily_rates(
        [(date(2026, 1, 2), Decimal("0.1")), (date(2026, 1, 3), Decimal("0.1"))]
    )
    assert [point.value for point in points] == pytest.approx([100.1, 100.2001])


@pytest.mark.asyncio
async def test_composite_cache_avoids_duplicate_provider_calls():
    yahoo = CountingProvider(
        "yahoo",
        BenchmarkMatch(symbol="^TEST", name="Test", exchange="SNP", provider="yahoo"),
    )
    b3 = CountingProvider(
        "b3",
        BenchmarkMatch(symbol="B3:DI", name="DI", exchange="B3", provider="b3"),
    )
    provider = CompositeBenchmarkProvider(yahoo=yahoo, di_b3=b3)

    first = await provider.search("test")
    second = await provider.search("test")
    assert first == second
    assert yahoo.search_calls == b3.search_calls == 1

    start, end = date(2026, 1, 1), date(2026, 1, 31)
    await provider.history("yahoo", "^TEST", start, end)
    await provider.history("yahoo", "^TEST", start, end)
    assert yahoo.history_calls == 1


@pytest.mark.asyncio
async def test_search_cache_tells_apart_queries_in_non_latin_scripts():
    class EchoProvider(BenchmarkProvider):
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
            self.queries.append(query)
            return [BenchmarkMatch(symbol=query, name=query, provider="yahoo")]

    yahoo = EchoProvider()
    provider = CompositeBenchmarkProvider(yahoo=yahoo, di_b3=EchoProvider())
    toyota = await provider.search("トヨタ")
    sony = await provider.search("ソニー")
    assert [match.symbol for match in toyota] == ["トヨタ"]
    assert [match.symbol for match in sony] == ["ソニー"]
    assert yahoo.queries == ["トヨタ", "ソニー"]
    # The same text, differing only in case and spacing, is still one entry.
    await provider.search(" ソニー ")
    assert yahoo.queries == ["トヨタ", "ソニー"]


def test_ttl_cache_is_bounded_lru():
    cache = _BoundedTTLCache[int](ttl_seconds=60, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # a becomes most recently used
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_ttl_cache_expires_and_prunes_entries(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.providers.benchmark.time.monotonic", lambda: now[0])
    cache = _BoundedTTLCache[int](ttl_seconds=15, max_entries=2)
    cache.set("expired", 1)
    now[0] += 16
    assert cache.get("expired") is None

    cache.set("another-expired", 2)
    now[0] += 16
    cache.set("fresh", 3)
    assert list(cache._values) == ["fresh"]


@pytest.mark.asyncio
async def test_search_keeps_results_when_another_provider_fails():
    b3 = CountingProvider(
        "b3",
        BenchmarkMatch(symbol="B3:DI", name="DI", exchange="B3", provider="b3"),
    )
    provider = CompositeBenchmarkProvider(yahoo=FailingProvider(), di_b3=b3)
    assert await provider.search("DI") == [b3.match]


@pytest.mark.asyncio
async def test_search_keeps_the_di_when_yahoo_fills_every_slot():
    class CrowdedYahoo(BenchmarkProvider):
        async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
            # "di" matches DIA, DIS and many more on Yahoo.
            return [
                BenchmarkMatch(symbol=f"DI{index}", name=f"DI{index}", provider="yahoo")
                for index in range(limit)
            ]

    provider = CompositeBenchmarkProvider(yahoo=CrowdedYahoo(), di_b3=B3DIBenchmarkProvider())
    results = await provider.search("di", 15)
    assert len(results) == 15
    assert (results[0].provider, results[0].symbol) == ("b3", "B3:DI")


@pytest.mark.asyncio
async def test_search_is_not_cached_while_a_source_is_failing():
    class CountingFailingProvider(FailingProvider):
        calls = 0

        async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
            self.calls += 1
            return await super().search(query, limit)

    yahoo = CountingFailingProvider()
    b3 = CountingProvider(
        "b3",
        BenchmarkMatch(symbol="B3:DI", name="DI", exchange="B3", provider="b3"),
    )
    provider = CompositeBenchmarkProvider(yahoo=yahoo, di_b3=b3)
    assert await provider.search("cdi") == [b3.match]
    assert await provider.search("cdi") == [b3.match]
    assert yahoo.calls == b3.search_calls == 2


def rate_history_loader(client):
    return DailyRateHistoryLoader(
        (
            DailyRateSource(
                name="Primary",
                url="https://primary.example/history",
                date_field="date",
                rate_field="rate",
                date_format="%d/%m/%Y",
                start_param="from",
                end_param="to",
            ),
            DailyRateSource(
                name="Fallback",
                url="https://fallback.example/observations",
                date_field="timestamp",
                rate_field="percentage",
                rows_key="observations",
            ),
        ),
        client=client,
    )


@pytest.mark.asyncio
async def test_history_uses_primary_source_when_healthy():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.params["from"] == "02/01/2026"
        assert request.url.params["to"] == "03/01/2026"
        return httpx.Response(
            200,
            json=[
                {"date": "02/01/2026", "rate": "0,1"},
                {"date": "03/01/2026", "rate": "0,2"},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        points = await rate_history_loader(client).history(date(2026, 1, 2), date(2026, 1, 3))
        assert not client.is_closed
    assert [point.value for point in points] == pytest.approx([100.1, 100.3002])
    assert [request.url.host for request in requests] == ["primary.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["dns", "timeout", "http", "rate_limit", "json", "shape", "empty"]
)
async def test_history_uses_fallback_source_when_primary_fails(failure):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "primary.example":
            if failure == "dns":
                raise httpx.ConnectError("DNS failed", request=request)
            if failure == "timeout":
                raise httpx.ReadTimeout("Timed out", request=request)
            if failure == "json":
                return httpx.Response(200, text="invalid JSON")
            if failure == "shape":
                return httpx.Response(200, json={"error": "down"})
            if failure == "empty":
                return httpx.Response(200, json=[])
            return httpx.Response(429 if failure == "rate_limit" else 503)
        assert request.url.host == "fallback.example"
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"timestamp": "1986-03-06T00:00:00-03:00", "percentage": 1},
                    {"timestamp": "2026-01-03T00:00:00-03:00", "percentage": 0.2},
                    {"timestamp": "2026-01-02T00:00:00-03:00", "percentage": 0.1},
                    {"timestamp": "2026-01-04T00:00:00-03:00", "percentage": 1},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        points = await rate_history_loader(client).history(date(2026, 1, 2), date(2026, 1, 3))
    assert [point.date for point in points] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert [point.value for point in points] == pytest.approx([100.1, 100.3002])
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["dns", "http", "rate_limit", "json", "shape", "date", "rate"])
async def test_history_reports_error_when_all_sources_fail(failure):
    def respond(request):
        if request.url.host == "primary.example" or failure == "dns":
            raise httpx.ConnectError("DNS failed", request=request)
        if failure == "http":
            return httpx.Response(503)
        if failure == "rate_limit":
            return httpx.Response(429)
        if failure == "json":
            return httpx.Response(200, text="invalid JSON")
        if failure == "shape":
            return httpx.Response(200, json={"error": "down"})
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"timestamp": "2026-01-03T00:00:00-03:00", "percentage": 0.2},
                    {
                        "timestamp": "bad" if failure == "date" else "2026-01-02T00:00:00-03:00",
                        "percentage": "NaN",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        expected = BenchmarkRateLimitedError if failure == "rate_limit" else BenchmarkProviderError
        with pytest.raises(expected):
            await rate_history_loader(client).history(date(2026, 1, 2), date(2026, 1, 3))


def _out_of_range_fallback(request):
    return httpx.Response(
        200,
        json={
            "observations": [
                {"timestamp": "1986-03-06T00:00:00-03:00", "percentage": 0.1},
            ]
        },
    )


@pytest.mark.asyncio
async def test_history_returns_empty_for_dates_without_observations():
    # Every source answers, and none has an observation in the dates asked.
    def respond(request):
        if request.url.host == "primary.example":
            return httpx.Response(200, json=[])
        return _out_of_range_fallback(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await rate_history_loader(client).history(date(2026, 1, 2), date(2026, 1, 3)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_points", [0, 1])
async def test_history_keeps_the_source_error_when_the_fallback_has_no_usable_history(
    fallback_points,
):
    def respond(request):
        if request.url.host == "primary.example":
            raise httpx.ConnectError("DNS failed", request=request)
        if fallback_points:
            return httpx.Response(
                200,
                json={
                    "observations": [{"timestamp": "2026-01-02T00:00:00-03:00", "percentage": 0.1}]
                },
            )
        return _out_of_range_fallback(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        # The failed source may have had the history: unavailable, not no data.
        with pytest.raises(BenchmarkProviderError) as raised:
            await rate_history_loader(client).history(date(2026, 1, 2), date(2026, 1, 3))
    assert not isinstance(raised.value, BenchmarkRateLimitedError)


@pytest.mark.asyncio
@pytest.mark.parametrize("huge_rate", ["1E+999999999", "1E+400"])
async def test_history_falls_back_when_a_rate_overflows_the_index(huge_rate):
    def respond(request):
        if request.url.host == "primary.example":
            return httpx.Response(
                200,
                json=[
                    {"date": "02/01/2026", "rate": huge_rate},
                    {"date": "03/01/2026", "rate": huge_rate},
                ],
            )
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"timestamp": "2026-01-02T00:00:00-03:00", "percentage": 0.1},
                    {"timestamp": "2026-01-03T00:00:00-03:00", "percentage": 0.2},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        loader = rate_history_loader(client)
        points = await loader.history(date(2026, 1, 2), date(2026, 1, 3))
        assert [point.value for point in points] == pytest.approx([100.1, 100.3002])
        with pytest.raises(BenchmarkProviderError):
            await loader._load(loader.sources[0], client, date(2026, 1, 2), date(2026, 1, 3))


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_count", [0, 1])
async def test_incomplete_history_can_be_retried_after_source_recovers(initial_count, monkeypatch):
    source = CountingProvider(
        "yahoo", BenchmarkMatch(symbol="^TEST", name="Test", exchange="SNP", provider="yahoo")
    )
    original_history = source.history
    calls = 0

    async def recovering_history(symbol, start_date, end_date):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [BenchmarkHistoryPoint(start_date, 100.0)] * initial_count
        return await original_history(symbol, start_date, end_date)

    monkeypatch.setattr(source, "history", recovering_history)
    provider = CompositeBenchmarkProvider(yahoo=source)
    start, end = date(2026, 1, 1), date(2026, 1, 31)
    assert len(await provider.history("yahoo", "^TEST", start, end)) == initial_count
    assert len(await provider.history("yahoo", "^TEST", start, end)) == 2
    assert calls == 2


def test_yahoo_history_raise_errors_option_exists_in_the_installed_yfinance():
    import inspect

    from yfinance.scrapers.history import PriceHistory

    assert "raise_errors" in inspect.signature(PriceHistory.history).parameters


@pytest.mark.asyncio
async def test_yahoo_history_reports_a_transport_failure_instead_of_no_data(monkeypatch):
    calls: list[dict] = []

    class FailingTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("raise_errors"):
                # yfinance's default: log the failure, return an empty frame.
                import pandas as pd

                return pd.DataFrame()
            raise TimeoutError("chart request timed out")

    monkeypatch.setattr("yfinance.Ticker", FailingTicker)
    with pytest.raises(BenchmarkProviderError) as raised:
        await YahooBenchmarkProvider().history("^GSPC", date(2026, 1, 1), date(2026, 1, 5))
    assert not isinstance(raised.value, BenchmarkRateLimitedError)
    assert calls and calls[0]["raise_errors"] is True


@pytest.mark.asyncio
async def test_yahoo_history_rate_limit_is_reported_as_rate_limited(monkeypatch):
    from yfinance.exceptions import YFRateLimitError

    class ThrottledTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            raise YFRateLimitError()

    monkeypatch.setattr("yfinance.Ticker", ThrottledTicker)
    with pytest.raises(BenchmarkRateLimitedError):
        await YahooBenchmarkProvider().history("^GSPC", date(2026, 1, 1), date(2026, 1, 5))


@pytest.mark.asyncio
async def test_yahoo_history_without_prices_is_empty(monkeypatch):
    from yfinance.exceptions import YFPricesMissingError

    class EmptyTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            raise YFPricesMissingError("^SPBTCFU", " (1d 2026-01-01 -> 2026-01-06)")

    monkeypatch.setattr("yfinance.Ticker", EmptyTicker)
    points = await YahooBenchmarkProvider().history("^SPBTCFU", date(2026, 1, 1), date(2026, 1, 5))
    assert points == []


def test_yahoo_history_carries_the_quote_currency_in_major_units(monkeypatch):
    import pandas as pd

    class FakeTicker:
        history_metadata = {"currency": "GBp"}

        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [700.0, 710.0]},
                index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
            )

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    points = YahooBenchmarkProvider._history_sync("ISF.L", date(2026, 1, 1), date(2026, 1, 5))
    assert [(point.value, point.currency) for point in points] == [(700.0, "GBP"), (710.0, "GBP")]


@pytest.mark.asyncio
async def test_di_history_is_quoted_in_reais(monkeypatch):
    provider = B3DIBenchmarkProvider()

    async def history(start_date, end_date):
        return [BenchmarkHistoryPoint(start_date, 100.0), BenchmarkHistoryPoint(end_date, 100.1)]

    monkeypatch.setattr(provider._history_loader, "history", history)
    points = await provider.history(DI_B3_SYMBOL, date(2026, 1, 2), date(2026, 1, 5))
    assert {point.currency for point in points} == {"BRL"}
