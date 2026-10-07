"""On-demand providers for searchable portfolio benchmarks.

This module is deliberately isolated from the normal asset quote path. Merely
visiting the Assets page never searches or downloads a benchmark: callers must
explicitly use the benchmark endpoints mounted by ``app.api.assets``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Generic, Optional, Protocol, TypeVar

import httpx

from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
    _rate_limit_exception_types,
)
from app.schemas.asset import BenchmarkMatch

logger = logging.getLogger(__name__)

DI_B3_SYMBOL = "B3:DI"
BCB_SGS_SOAP_URL = "https://www3.bcb.gov.br/wssgs/services/FachadaWSSGS"
BCB_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados"
IPEADATA_SERIES_URL = "https://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{series}')"
YAHOO_LOOKUP_URL = "https://query2.finance.yahoo.com/v1/finance/lookup"


@dataclass(frozen=True)
class BenchmarkHistoryPoint:
    date: date
    value: float


class BenchmarkProviderError(Exception):
    """An upstream benchmark source could not fulfil the request."""


class BenchmarkRateLimitedError(BenchmarkProviderError):
    """An upstream source asked Securo to back off."""


class BenchmarkProvider:
    name = "abstract"

    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        raise NotImplementedError

    async def history(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        raise NotImplementedError


class YahooBenchmarkProvider(BenchmarkProvider):
    """Dynamic index discovery and adjusted historical closes from Yahoo."""

    name = "yahoo"

    def __init__(self, market_provider: Optional[MarketPriceProvider] = None) -> None:
        # An override keeps the provider independently testable and allows an
        # installation to reuse another market search implementation. The
        # default uses Yahoo's typed lookup endpoint: its generic autocomplete
        # often returns only futures for searches such as "500" or "Nasdaq".
        self.market_provider = market_provider

    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        try:
            if self.market_provider is None:
                return await asyncio.to_thread(self._lookup_sync, query, limit)

            # Custom market searches may mix equities, funds and indices. Ask
            # for extra candidates and retain only real INDEX results.
            matches = await self.market_provider.search(query, limit=min(max(limit * 4, 20), 80))
        except MarketPriceRateLimitedError as exc:
            raise BenchmarkRateLimitedError(str(exc)) from exc
        except _rate_limit_exception_types() as exc:
            raise BenchmarkRateLimitedError("Yahoo Finance rate-limited index search") from exc
        except BenchmarkProviderError:
            raise
        except Exception as exc:  # pragma: no cover - upstream/network drift
            logger.warning("Yahoo benchmark search failed for %r: %s", query, exc)
            raise BenchmarkProviderError("Could not search Yahoo Finance indices") from exc
        return [
            BenchmarkMatch(
                symbol=match.symbol,
                name=match.name or match.symbol,
                exchange=match.exchange,
                provider=self.name,
            )
            for match in matches
            if (match.quote_type or "").upper() == "INDEX"
        ][:limit]

    @staticmethod
    def _lookup_sync(query: str, limit: int) -> list[BenchmarkMatch]:
        """Search Yahoo's instrument lookup with an upstream INDEX filter."""
        from yfinance.data import YfData

        normalized = (query or "").strip()
        if not normalized:
            return []
        response = YfData().get(
            YAHOO_LOOKUP_URL,
            params={
                "formatted": "true",
                "lang": "en-US",
                "region": "US",
                "query": normalized,
                "type": "index",
                "count": min(max(limit, 15), 80),
                "start": 0,
            },
            timeout=30,
        )
        if response.status_code == 429:
            raise BenchmarkRateLimitedError("Yahoo Finance rate-limited index search")
        if response.status_code >= 400:
            raise BenchmarkProviderError(
                f"Yahoo Finance index search returned HTTP {response.status_code}"
            )
        payload = response.json()
        result_sets = payload.get("finance", {}).get("result") or []
        documents = [
            document
            for result_set in result_sets
            if isinstance(result_set, dict)
            for document in (result_set.get("documents") or [])
            if isinstance(document, dict)
        ]

        matches: list[BenchmarkMatch] = []
        seen: set[str] = set()
        for document in documents:
            symbol = str(document.get("symbol") or "").strip()
            quote_type = str(document.get("quoteType") or "").upper()
            if not symbol or quote_type != "INDEX" or symbol in seen:
                continue
            seen.add(symbol)
            matches.append(
                BenchmarkMatch(
                    symbol=symbol,
                    name=(document.get("longName") or document.get("shortName") or symbol),
                    exchange=(
                        document.get("exchangeName")
                        or document.get("exchDisp")
                        or document.get("exchange")
                    ),
                    provider=YahooBenchmarkProvider.name,
                )
            )
            if len(matches) >= limit:
                break
        return matches

    async def history(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            return []
        try:
            return await asyncio.to_thread(self._history_sync, normalized, start_date, end_date)
        except _rate_limit_exception_types() as exc:
            raise BenchmarkRateLimitedError(
                f"Yahoo Finance rate-limited history for {normalized}"
            ) from exc
        except BenchmarkProviderError:
            raise
        except Exception as exc:  # pragma: no cover - upstream/network drift
            logger.warning("Yahoo benchmark history failed for %s: %s", normalized, exc)
            raise BenchmarkProviderError("Could not load benchmark history") from exc

    @staticmethod
    def _history_sync(symbol: str, start_date: date, end_date: date) -> list[BenchmarkHistoryPoint]:
        import yfinance as yf

        # ``end`` is exclusive. Ticker.history gives flat OHLC columns for one
        # symbol and auto_adjust uses the provider's split/dividend adjustment.
        frame = yf.Ticker(symbol).history(
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        if frame is None or frame.empty or "Close" not in frame.columns:
            return []

        values: dict[date, float] = {}
        for raw_date, raw_value in frame["Close"].items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN without importing pandas here
                continue
            point_date = raw_date.date() if hasattr(raw_date, "date") else raw_date
            if start_date <= point_date <= end_date:
                values[point_date] = value
        return [BenchmarkHistoryPoint(d, values[d]) for d in sorted(values)]


def _normalize_search(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def accumulate_daily_rates(rates: list[tuple[date, Decimal]]) -> list[BenchmarkHistoryPoint]:
    """Compound daily percentage rates into an index starting at 100."""
    by_date = dict(rates)
    index_value = Decimal("100")
    points: list[BenchmarkHistoryPoint] = []
    for point_date in sorted(by_date):
        index_value *= Decimal("1") + by_date[point_date] / Decimal("100")
        points.append(BenchmarkHistoryPoint(point_date, float(index_value)))
    return points


@dataclass(frozen=True)
class DailyRateSource:
    """Describe a source's wire format without coupling callers to its API."""

    name: str
    url: str
    date_field: str
    rate_field: str
    rows_key: Optional[str] = None
    date_format: Optional[str] = None
    params: Optional[dict[str, str]] = None
    start_param: Optional[str] = None
    end_param: Optional[str] = None

    async def rows(self, client: httpx.AsyncClient, start_date: date, end_date: date) -> object:
        """Fetch the raw JSON observations; subclasses override for other transports."""
        params = dict(self.params or {})
        date_format = self.date_format or "%Y-%m-%d"
        if self.start_param:
            params[self.start_param] = start_date.strftime(date_format)
        if self.end_param:
            params[self.end_param] = end_date.strftime(date_format)
        response = await client.get(self.url, params=params)
        if response.status_code == 429:
            raise BenchmarkRateLimitedError(f"{self.name} rate-limited benchmark history")
        response.raise_for_status()
        payload = response.json()
        if self.rows_key is not None and isinstance(payload, dict):
            return payload.get(self.rows_key)
        return payload


@dataclass(frozen=True)
class SGSSoapRateSource(DailyRateSource):
    """SGS transport for any configured daily-percentage series."""

    series: int = 0

    async def rows(
        self, client: httpx.AsyncClient, start_date: date, end_date: date
    ) -> list[dict[str, str]]:
        body = f"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:enc="http://schemas.xmlsoap.org/soap/encoding/"
            xmlns:p="http://publico.ws.casosdeuso.sgs.pec.bcb.gov.br">
          <s:Body><p:getValoresSeriesXML s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
            <in0 xsi:type="enc:Array" enc:arrayType="xsd:long[1]">
              <item xsi:type="xsd:long">{int(self.series)}</item>
            </in0>
            <in1 xsi:type="xsd:string">{start_date:%d/%m/%Y}</in1>
            <in2 xsi:type="xsd:string">{end_date:%d/%m/%Y}</in2>
          </p:getValoresSeriesXML></s:Body>
        </s:Envelope>"""
        response = await client.post(
            self.url,
            content=body,
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        )
        if response.status_code == 429:
            raise BenchmarkRateLimitedError(f"{self.name} rate-limited benchmark history")
        # SGS uses HTTP 500 with a SOAP business fault for empty date ranges.
        if response.status_code not in (200, 500):
            response.raise_for_status()
        envelope = ET.fromstring(response.content)
        fault = envelope.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            message = fault.findtext("faultstring", "")
            if message.strip() == (
                "br.gov.bcb.pec.sgs.comum.excecoes.SGSNegocioException: Value(s) not found"
            ):
                return []
            raise BenchmarkProviderError(f"SOAP fault from {self.name}")
        response.raise_for_status()
        result = envelope.find(".//getValoresSeriesXMLReturn")
        if result is not None and result.get("href", "").startswith("#"):
            target_id = result.get("href", "")[1:]
            result = next((e for e in envelope.iter() if e.get("id") == target_id), None)
        if result is None or not result.text:
            raise BenchmarkProviderError(f"Missing SOAP history from {self.name}")
        payload = ET.fromstring(result.text)
        if payload.tag != "SERIES":
            raise BenchmarkProviderError(f"Unexpected SOAP history from {self.name}")
        series = payload.find(f"SERIE[@ID='{int(self.series)}']")
        if series is None:
            raise BenchmarkProviderError(f"Missing series from {self.name}")
        rows = []
        for item in series.findall("ITEM"):
            value = (item.findtext(self.rate_field) or "").strip()
            # Unpublished and blocked observations must never become zero rates.
            if not value or item.findtext("BLOQUEADO", "false").strip().lower() == "true":
                continue
            rows.append(
                {self.date_field: item.findtext(self.date_field, ""), self.rate_field: value}
            )
        return rows


class DailyRateHistoryLoader:
    """Load exact daily rates from configured sources in fallback order."""

    def __init__(
        self, sources: tuple[DailyRateSource, ...], *, client: Optional[httpx.AsyncClient] = None
    ) -> None:
        self.sources = sources
        self._client = client

    async def history(self, start_date: date, end_date: date) -> list[BenchmarkHistoryPoint]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        last_error: Optional[BenchmarkProviderError] = None
        partial: list[BenchmarkHistoryPoint] = []
        try:
            for source in self.sources:
                try:
                    points = await self._load(source, client, start_date, end_date)
                    last_error = None
                    # A single observation cannot form a return, so let a
                    # later source with a usable history take precedence.
                    if len(points) >= 2:
                        return points
                    partial = partial or points
                except BenchmarkProviderError as exc:
                    last_error = exc
                    logger.warning("Benchmark history source %s failed: %s", source.name, exc)
            if partial:
                return partial
            if last_error is not None:
                raise last_error
            return []
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    async def _load(
        source: DailyRateSource, client: httpx.AsyncClient, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        try:
            rows = await source.rows(client, start_date, end_date)
            if not isinstance(rows, list):
                raise BenchmarkProviderError(f"Unexpected history response from {source.name}")

            # Some APIs ignore date filters and return their entire series.
            # Always restrict the observations before compounding the rates.
            rates: list[tuple[date, Decimal]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise BenchmarkProviderError(f"Invalid observation from {source.name}")
                raw_date = str(row.get(source.date_field, ""))
                try:
                    point_date = (
                        datetime.strptime(raw_date, source.date_format).date()
                        if source.date_format is not None
                        else datetime.fromisoformat(raw_date).date()
                    )
                except ValueError as exc:
                    raise BenchmarkProviderError(f"Invalid date from {source.name}") from exc
                if not start_date <= point_date <= end_date:
                    continue
                try:
                    rate = Decimal(str(row.get(source.rate_field)).replace(",", "."))
                except InvalidOperation as exc:
                    raise BenchmarkProviderError(f"Invalid daily rate from {source.name}") from exc
                if not rate.is_finite() or rate <= -100:
                    raise BenchmarkProviderError(f"Invalid daily rate from {source.name}")
                rates.append((point_date, rate))
            return accumulate_daily_rates(rates)
        except BenchmarkProviderError:
            raise
        except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
            raise BenchmarkProviderError(f"Could not load history from {source.name}") from exc


class B3DIBenchmarkProvider(BenchmarkProvider):
    """A benchmark catalog entry backed by configured daily rate sources."""

    name = "b3"
    _search_text = _normalize_search("Indice DI B3 CDI DI Cetip taxa DI deposito interfinanceiro")
    _search_tokens = frozenset(_search_text.split())

    def __init__(self, *, client: Optional[httpx.AsyncClient] = None) -> None:
        self._history_loader = DailyRateHistoryLoader(
            (
                SGSSoapRateSource(
                    name="BCB SGS SOAP",
                    url=BCB_SGS_SOAP_URL,
                    series=12,
                    date_field="DATA",
                    rate_field="VALOR",
                    date_format="%d/%m/%Y",
                ),
                DailyRateSource(
                    name="BCB SGS",
                    url=BCB_SGS_URL.format(series=12),
                    date_field="data",
                    rate_field="valor",
                    date_format="%d/%m/%Y",
                    params={"formato": "json"},
                    start_param="dataInicial",
                    end_param="dataFinal",
                ),
                DailyRateSource(
                    name="Ipeadata",
                    url=IPEADATA_SERIES_URL.format(series="SGS366_CDI366"),
                    date_field="VALDATA",
                    rate_field="VALVALOR",
                    rows_key="value",
                ),
            ),
            client=client,
        )

    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        normalized = _normalize_search(query)
        tokens = normalized.split()
        if not normalized or not all(
            any(candidate.startswith(token) for candidate in self._search_tokens)
            for token in tokens
        ):
            return []
        return [
            BenchmarkMatch(
                symbol=DI_B3_SYMBOL,
                name="Índice DI B3",
                exchange="B3",
                provider=self.name,
            )
        ][:limit]

    async def history(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[BenchmarkHistoryPoint]:
        if (symbol or "").strip().upper() != DI_B3_SYMBOL:
            return []
        return await self._history_loader.history(start_date, end_date)


T = TypeVar("T")


class _BoundedTTLCache(Generic[T]):
    def __init__(self, *, ttl_seconds: int, max_entries: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._values: OrderedDict[object, tuple[float, T]] = OrderedDict()

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._values.items() if expires_at <= now]
        for key in expired:
            del self._values[key]

    def get(self, key: object) -> Optional[T]:
        entry = self._values.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._values[key]
            return None
        self._values.move_to_end(key)
        return value

    def set(self, key: object, value: T) -> None:
        self._prune_expired()
        self._values[key] = (time.monotonic() + self.ttl_seconds, value)
        self._values.move_to_end(key)
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)


class BenchmarkHistoryRouter(Protocol):
    async def history(
        self,
        provider_name: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[BenchmarkHistoryPoint]: ...


class CompositeBenchmarkProvider:
    """Route histories to their source and search every source independently."""

    name = "composite"

    def __init__(
        self,
        yahoo: Optional[BenchmarkProvider] = None,
        di_b3: Optional[BenchmarkProvider] = None,
    ) -> None:
        self.providers: dict[str, BenchmarkProvider] = {
            "yahoo": yahoo or YahooBenchmarkProvider(),
            "b3": di_b3 or B3DIBenchmarkProvider(),
        }
        self._search_cache = _BoundedTTLCache[list[BenchmarkMatch]](ttl_seconds=15 * 60)
        self._history_cache = _BoundedTTLCache[list[BenchmarkHistoryPoint]](ttl_seconds=60 * 60)

    async def search(self, query: str, limit: int = 15) -> list[BenchmarkMatch]:
        key = (_normalize_search(query), limit)
        cached = self._search_cache.get(key)
        if cached is not None:
            return list(cached)

        results = await asyncio.gather(
            *(provider.search(query, limit) for provider in self.providers.values()),
            return_exceptions=True,
        )
        matches: list[BenchmarkMatch] = []
        successes = 0
        rate_limited = False
        failed = False
        for result in results:
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                if isinstance(result, BenchmarkRateLimitedError):
                    rate_limited = True
                else:
                    failed = True
                    logger.warning("Benchmark search source failed: %s", result)
                continue
            successes += 1
            matches.extend(result)

        if successes == 0:
            if rate_limited:
                raise BenchmarkRateLimitedError("Benchmark sources are rate-limiting requests")
            raise BenchmarkProviderError("All benchmark search sources failed")
        if not matches and rate_limited:
            raise BenchmarkRateLimitedError("A benchmark source is rate-limiting requests")
        if not matches and failed:
            raise BenchmarkProviderError("A benchmark search source failed")

        deduplicated: list[BenchmarkMatch] = []
        seen: set[tuple[str, str]] = set()
        for match in matches:
            identity = (match.provider, match.symbol)
            if identity in seen:
                continue
            seen.add(identity)
            deduplicated.append(match)
        deduplicated = deduplicated[:limit]
        self._search_cache.set(key, deduplicated)
        return list(deduplicated)

    async def history(
        self,
        provider_name: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[BenchmarkHistoryPoint]:
        provider_key = (provider_name or "").strip().casefold()
        provider = self.providers.get(provider_key)
        if provider is None:
            return []
        normalized = (symbol or "").strip().upper()
        key = (provider_key, normalized, start_date, end_date)
        cached = self._history_cache.get(key)
        if cached is not None:
            return list(cached)
        points = await provider.history(normalized, start_date, end_date)
        if len(points) >= 2:
            self._history_cache.set(key, points)
        return list(points)


_provider: Optional[CompositeBenchmarkProvider] = None


def get_benchmark_provider() -> CompositeBenchmarkProvider:
    global _provider
    if _provider is None:
        _provider = CompositeBenchmarkProvider()
    return _provider
