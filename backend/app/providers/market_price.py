"""Market-price data providers (stocks, ETFs, crypto, funds).

Asset valuation from a live public quote — the user enters a ticker and
quantity, the provider returns the price, and Securo tracks the value
over time. This is deliberately separate from `BankProvider`: there's no
per-user authentication and no accounts/transactions, just a symbol
lookup backed by a public data source (Yahoo Finance today).

Why a thin abstraction: swapping Yahoo out (for Stooq, Alpha Vantage,
CoinGecko, or a user-chosen source) is a realistic future, and keeping
the asset service off the concrete provider makes those swaps non-events.

Calls to Yahoo go through ``market_data_guard``: a cache shared by every
workspace (keyed by the public symbol only) and an instance-wide throttle,
because all workspaces of an instance reach Yahoo from the same address.

yfinance usage notes (checked against v1.8.0, the version in ``uv.lock``):
  * ``yfinance.Search(query, max_results=..., news_count=..., enable_fuzzy_query=...)``
    returns a ``.quotes`` list of dicts with keys like ``symbol``, ``shortname``,
    ``longname``, ``exchange``, ``exchDisp``, ``quoteType``.
  * ``yfinance.Ticker(symbol).fast_info`` exposes camelCase keys —
    ``lastPrice``, ``previousClose``, ``currency``, ``exchange``, ``quoteType``.
    snake_case keys are silently accepted too, but camelCase is the public API.
  * ``yf.config.network.retries`` enables transient-error retries (exponential
    backoff). Set once at module import so both Search and Ticker benefit.
  * ``YFRateLimitError`` is raised when Yahoo rate-limits us — we surface it
    so the caller can decide (UI toast vs. scheduled-task skip). yfinance
    re-raises it even while ``yf.config.debug.hide_exceptions`` (on by
    default) swallows other errors.
  * ``yf.download`` makes one HTTP request per ticker (a loop over
    ``_download_one`` in ``yfinance/multi.py``), not one per batch.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple, Optional

from app.providers import market_data_guard
from app.providers.favicon import favicon_url_for
from app.schemas.asset import MarketSymbolMatch, MarketSymbolQuote

logger = logging.getLogger(__name__)


class LatestClose(NamedTuple):
    """A symbol's latest price and the trading day it closed on.

    ``on`` is None when the provider can't say which day the price is from.
    """

    price: Decimal
    on: Optional[date]


# Yahoo Finance occasionally reports prices in minor units (pence, cents).
# These are not valid ISO 4217 codes and, if kept raw, produce 100x-wrong
# valuations. Same table Sure uses (see app/models/provider/yahoo_finance.rb).
_MINOR_UNIT_CURRENCIES: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),  # British pence → pounds (e.g. IITU.L)
    "ZAc": ("ZAR", 0.01),  # South African cents → rand (e.g. JSE.JO)
}


def _close_to_cache(close: LatestClose) -> dict:
    return {"price": str(close.price), "on": close.on.isoformat() if close.on else None}


def _close_from_cache(raw: object) -> Optional[LatestClose]:
    """A cached close, or ``None`` for a miss or anything unreadable."""
    if not isinstance(raw, dict):
        return None
    try:
        price = Decimal(raw["price"])
        on = date.fromisoformat(raw["on"]) if raw.get("on") else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    if not price.is_finite():
        return None
    return LatestClose(price, on)


def _normalize_currency_and_price(currency: str, price: float) -> tuple[str, float]:
    conv = _MINOR_UNIT_CURRENCIES.get(currency)
    if conv is None:
        return currency, price
    code, multiplier = conv
    return code, price * multiplier


class MarketPriceProviderError(Exception):
    """Provider-level failure — propagated so callers can return a useful error."""


class MarketPriceRateLimitedError(MarketPriceProviderError):
    """Upstream (Yahoo) rate-limited us. Caller should back off, not crash."""


class MarketPriceProvider(ABC):
    """Abstract interface for looking up tradable symbols and their prices."""

    name: str = "abstract"

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        """Return ticker suggestions matching the user's query."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        """Return the latest price for a single symbol, or None if unknown."""

    async def get_quotes(self, symbols: list[str]) -> dict[str, Optional[MarketSymbolQuote]]:
        """Batch variant — default is a sequential get_quote loop.

        Subclasses may override with a true bulk endpoint. Stays sequential
        by default so we don't accidentally thunder-herd the upstream API.
        """
        out: dict[str, Optional[MarketSymbolQuote]] = {}
        for sym in symbols:
            out[sym] = await self.get_quote(sym)
        return out

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        """Batch-fetch only the latest price for each symbol.

        Lighter than `get_quotes`: scheduled refresh doesn't need name,
        exchange, or quote_type — just the number. Subclasses should
        implement this with a true bulk endpoint (one HTTP request)
        whenever possible; the default here is a safe sequential fallback
        so the interface stays compatible with simpler providers.
        """
        out: dict[str, Optional[Decimal]] = {}
        for sym in symbols:
            key = sym.strip().upper()
            if not key:
                continue
            quote = await self.get_quote(key)
            if quote is None or quote.price is None:
                out[key] = None
            else:
                out[key] = Decimal(str(quote.price))
        return out

    async def get_latest_closes(self, symbols: list[str]) -> dict[str, Optional[LatestClose]]:
        """`get_latest_prices` with the trading day each price closed on.

        The scheduled refresh uses the day to tell a close from before a
        recorded split. The default doesn't know the day.
        """
        prices = await self.get_latest_prices(symbols)
        return {s: LatestClose(p, None) if p is not None else None for s, p in prices.items()}


class YFinanceProvider(MarketPriceProvider):
    """Yahoo Finance provider backed by the ``yfinance`` Python library.

    ``yfinance`` is synchronous and hits Yahoo's HTTP endpoints internally,
    so every call is wrapped in ``asyncio.to_thread`` to keep the FastAPI
    event loop responsive.
    """

    name = "yfinance"

    # How many transient-network retries yfinance should perform internally.
    # yfinance uses exponential backoff (1s, 2s, 4s...) so 2 retries adds at
    # most ~3s to any call — cheap enough to keep defaults sane.
    _RETRIES = 2

    def __init__(self) -> None:
        # Imported lazily so a broken or missing yfinance install only hits
        # the market-price code paths — the rest of Securo keeps booting.
        import yfinance as yf

        # Global config singleton — the canonical knob in yfinance 1.x.
        # Wrap in try/except because older versions use a different shape.
        try:
            yf.config.network.retries = self._RETRIES
        except AttributeError:
            pass

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        q = (query or "").strip()
        if not q:
            return []
        raw = await market_data_guard.cached(
            "search",
            f"{limit}:{q.casefold()}",
            market_data_guard.SEARCH_TTL_SECONDS,
            lambda: self._call_upstream(self._search_sync, q, limit),
        )
        matches: list[MarketSymbolMatch] = []
        for quote in raw or []:
            symbol = quote.get("symbol")
            if not symbol:
                continue
            matches.append(
                MarketSymbolMatch(
                    symbol=symbol,
                    # Yahoo's search result uses lowercase `longname`/`shortname`
                    # (not the camelCase the rest of yfinance uses).
                    name=quote.get("longname") or quote.get("shortname"),
                    exchange=quote.get("exchDisp") or quote.get("exchange"),
                    quote_type=(quote.get("quoteType") or "").upper() or None,
                )
            )
        return matches

    async def _call_upstream(self, fn, *args):
        """Run one blocking yfinance call under the instance-wide throttle.

        No free slot, or Yahoo cooling down after a 429, raises the same
        rate-limit error a 429 does, so routes answer "try again" and never
        a 500. A 429 from Yahoo starts that cooldown for every caller.
        """
        if not await market_data_guard.take_call_slot():
            raise MarketPriceRateLimitedError("Market data calls are throttled; try again shortly")
        try:
            return await asyncio.to_thread(fn, *args)
        except MarketPriceRateLimitedError:
            await market_data_guard.start_cooldown()
            raise

    # Tickers per `yf.download` call. yfinance still fetches them one HTTP
    # request each; the chunk only bounds how much one failed call loses.
    _BATCH_CHUNK_SIZE = 100

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        """Latest close per ticker via ``yfinance.download``.

        ``download`` costs one HTTP request per ticker, the same count as
        looping ``get_quote``'s price lookup, but skips the ``info`` fetch
        and the per-call overhead. The tradeoff: ``download`` returns
        prices only (no currency/name/etc.).
        That's fine here because ``Asset.currency`` is already cached from
        creation time; we just update the price and let ``current_value``
        recompute from ``units × last_price``.

        Note on minor-unit currencies (GBp, ZAc on LSE/JSE): ``download``
        returns the raw pence/cents close, so ``*.L`` / ``*.JO`` tickers get
        a follow-up ``fast_info.currency`` lookup and are scaled ×0.01 to
        match the GBP/ZAR normalization ``get_quote`` applies at creation.
        If that lookup fails for a ticker its price is returned as ``None``
        rather than raw, so the caller falls back to ``get_quote``.
        """
        closes = await self.get_latest_closes(symbols)
        return {s: c.price if c is not None else None for s, c in closes.items()}

    async def get_latest_closes(self, symbols: list[str]) -> dict[str, Optional[LatestClose]]:
        """The batch download behind `get_latest_prices`, keeping each close's day."""
        if not symbols:
            return {}
        unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        if not unique:
            return {}

        out: dict[str, Optional[LatestClose]] = {s: None for s in unique}
        # The scheduled sweep asks once per workspace, and every workspace
        # holding the same ticker would otherwise download it again. A close
        # is public and the same for everyone, so it is shared through the
        # cache for as long as a quote is.
        missing: list[str] = []
        for sym in unique:
            hit = _close_from_cache(await market_data_guard.read("close", sym))
            if hit is None:
                missing.append(sym)
            else:
                out[sym] = hit
        if not missing:
            return out

        refused = False
        if await market_data_guard.cooling_down():
            # Yahoo refused a moment ago: the cached closes are the answer
            # and the rest waits for the next run instead of asking again.
            missing = []
            refused = True
        for i in range(0, len(missing), self._BATCH_CHUNK_SIZE):
            chunk = missing[i : i + self._BATCH_CHUNK_SIZE]
            if not await market_data_guard.take_call_slot():
                # The instance is out of call slots: a batch must not go
                # around the throttle every other caller waits on.
                refused = True
                break
            try:
                chunk_prices, chunk_refused = await asyncio.to_thread(
                    self._download_closes_sync, chunk
                )
            except (_rate_limit_exception_types() + (MarketPriceRateLimitedError,)):
                await market_data_guard.start_cooldown()
                refused = True
                break

            # Tickers `yf.download` could not price at all. A symbol dropped
            # further down for an unknown minor unit is not one of these: it
            # did answer, so it says nothing about Yahoo refusing.
            unanswered = [s for s, p in chunk_prices.items() if p is None]

            # Normalize minor-unit prices (GBp → GBP, ZAc → ZAR, both ×0.01).
            # yf.download returns raw pence/cents for LSE/JSE tickers without
            # a currency tag; we fetch fast_info.currency only for the tickers
            # that actually need it (*.L and *.JO suffixes) to keep the extra
            # round-trips minimal.
            candidates = [
                s for s, p in chunk_prices.items()
                if p is not None and (s.endswith(".L") or s.endswith(".JO"))
            ]
            if chunk_refused and candidates:
                # `_get_currencies_sync` talks to Yahoo by itself, outside the
                # throttle and the cooldown, so right after a refusal it would
                # send one uncooled request per minor-unit ticker in the batch
                # — up to a hundred. The closes cannot be scaled without their
                # currency, so they are dropped here and fall back to
                # `get_quote`, whose first call hits the cooldown started below.
                for sym in candidates:
                    chunk_prices[sym] = None
            elif candidates:
                try:
                    currencies = await asyncio.to_thread(
                        self._get_currencies_sync, candidates
                    )
                except Exception as e:  # pragma: no cover (upstream flakiness, not logic)
                    logger.warning(
                        "minor-unit currency check failed for %s: %s", candidates, e
                    )
                    currencies = {}
                for sym in candidates:
                    currency = currencies.get(sym)
                    if not currency:
                        # Unit unknown: a raw pence/cents close could be
                        # 100× off, so drop it and let the caller fall back
                        # to ``get_quote``, which normalizes on its own.
                        chunk_prices[sym] = None
                        continue
                    conv = _MINOR_UNIT_CURRENCIES.get(currency)
                    close = chunk_prices[sym]
                    if conv is not None and close is not None:
                        chunk_prices[sym] = close._replace(price=close.price * Decimal(str(conv[1])))

            # The closes that did come back are part of the answer as soon as
            # they arrive: a refusal found below must not take them back.
            out.update(chunk_prices)
            for sym, close in chunk_prices.items():
                if close is not None:
                    await market_data_guard.write(
                        "close", sym, _close_to_cache(close), market_data_guard.PRICE_TTL_SECONDS
                    )

            if chunk_refused:
                # The batch itself reported a 429 for one of its tickers. The
                # frame cannot say which, and asking again may well succeed —
                # the block lifts between calls as often as not — so the
                # cooldown starts from what the batch said instead of waiting
                # for the probe to reproduce it.
                await market_data_guard.start_cooldown()
                refused = True
                break

            if unanswered:
                # `yf.download` records a per-ticker failure instead of
                # raising it, so a rate limit inside the batch never reaches
                # the handler above — and it can sit next to tickers that did
                # answer, which a whole-chunk check would never look at. Every
                # unanswered ticker is probed until one refuses: a delisted
                # ticker answering without a price says nothing about the next
                # one, whose 429 is hidden the same way. Probing stops at the
                # first refusal, so the extra calls only happen while Yahoo is
                # still answering them.
                for symbol in unanswered:
                    try:
                        await self._call_upstream(self._price_sync, symbol)
                    except MarketPriceRateLimitedError:
                        refused = True
                        break
                if refused:
                    break

        if refused:
            # A refusal partway through must not throw away the chunks that
            # already answered: the caller commits what came in and lets the
            # remaining tickers (still None here) fall back per holding, where
            # the cooldown stops them at the first call. Only a batch that got
            # nothing at all is worth raising for.
            if all(price is None for price in out.values()):
                raise MarketPriceRateLimitedError(
                    "Yahoo Finance rate-limited the batch price download"
                )
        return out

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        raw = await market_data_guard.cached(
            "price",
            sym,
            market_data_guard.PRICE_TTL_SECONDS,
            lambda: self._price_for_cache(sym),
        )
        if raw is None:
            return None
        price = raw.get("price")
        currency = raw.get("currency")
        if price is None or not currency:
            return None
        # The name and website only matter for display; a throttled or
        # failed lookup still returns the price.
        try:
            info = await market_data_guard.cached(
                "info",
                sym,
                market_data_guard.INFO_TTL_SECONDS,
                lambda: self._call_upstream(self._info_sync, sym),
            )
        except MarketPriceRateLimitedError:
            info = None
        info = info or {}
        quote_type = raw.get("quote_type") or info.get("quote_type")
        normalized_currency, normalized_price = _normalize_currency_and_price(
            currency, float(price)
        )
        return MarketSymbolQuote(
            symbol=sym,
            name=info.get("name"),
            exchange=raw.get("exchange"),
            currency=normalized_currency,
            price=normalized_price,
            quote_type=quote_type,
            logo_url=_logo_url_for(info.get("website")),
        )

    async def _price_for_cache(self, symbol: str) -> Optional[dict]:
        """The price payload, or ``None`` when it cannot be used.

        ``cached`` does not store ``None``, so a quote Yahoo answered with no
        price (``lastPrice`` and ``previousClose`` both empty) is retried on
        the next request instead of pinning a 404 on the symbol for the whole
        price TTL.
        """
        raw = await self._call_upstream(self._price_sync, symbol)
        if not raw or raw.get("price") is None or not raw.get("currency"):
            return None
        return raw

    # ---- sync helpers (called via asyncio.to_thread) ----

    @staticmethod
    def _search_sync(query: str, limit: int) -> Optional[list[dict]]:
        import yfinance as yf

        try:
            # Search signature (yfinance 1.3.x): Search(query, max_results=8,
            # news_count=8, lists_count=8, include_cb=True, enable_fuzzy_query=False,
            # session=None, timeout=30, raise_errors=True). We turn news off and
            # let errors propagate so we can log them.
            result = yf.Search(
                query,
                max_results=limit,
                news_count=0,
                enable_fuzzy_query=True,
                raise_errors=False,
            )
            quotes = getattr(result, "quotes", None)
            response = getattr(result, "response", None)
            if isinstance(quotes, list):
                if not quotes and not response:
                    # yfinance hides a JSON decode failure (``hide_exceptions``
                    # is on by default) and leaves its response empty instead
                    # of raising. That is a failed search, not "no matches",
                    # so it must not be cached as one for the whole search TTL.
                    logger.warning("yfinance search returned no payload for %r", query)
                    return None
                return quotes
            # Defensive fallback for any future shape drift.
            return list((response or {}).get("quotes") or [])
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError("Yahoo Finance rate-limited the search")
        except Exception as e:  # pragma: no cover (upstream flakiness, not logic)
            logger.warning("yfinance search failed for %r: %s", query, e)
            # None, not [], so a failure is not cached as "no matches".
            return None

    @staticmethod
    def _get_currencies_sync(symbols: list[str]) -> dict[str, Optional[str]]:
        """Return ``fast_info.currency`` for each symbol.

        Used by ``get_latest_prices`` to identify minor-unit tickers (GBp,
        ZAc) after a ``yf.download`` batch, which returns raw prices without
        currency metadata. Only called for the small subset of tickers whose
        exchange suffix suggests a minor-unit quote (``*.L``, ``*.JO``).
        """
        import yfinance as yf

        out: dict[str, Optional[str]] = {}
        for sym in symbols:
            try:
                currency = _fast_info_value(yf.Ticker(sym).fast_info, "currency")
                out[sym] = str(currency) if currency is not None else None
            except Exception:
                out[sym] = None
        return out

    @staticmethod
    def _download_closes_sync(
        symbols: list[str],
    ) -> tuple[dict[str, Optional[LatestClose]], bool]:
        """Run ``yf.download`` and extract the last close per symbol, with its day.

        ``yf.download`` returns a pandas DataFrame with (symbol, field)
        MultiIndex columns, one symbol or many. Period is
        ``"5d"`` so we span weekends and market holidays while still
        getting today's close once the market has ticked. Before the market
        opens, the last row is the previous trading day.

        The boolean says whether the batch recorded a Yahoo rate limit for
        any of its tickers. It is a return value and not an exception
        because the tickers this call did price are worth keeping, and only
        the caller knows how to stop there.
        """
        import pandas as pd
        import yfinance as yf

        out: dict[str, Optional[LatestClose]] = {s: None for s in symbols}
        if not symbols:
            return out, False

        yf_logger = logging.getLogger("yfinance")
        capture = _BatchRateLimitCapture()
        yf_logger.addHandler(capture)
        try:
            df = yf.download(
                tickers=symbols,
                period="5d",
                interval="1d",
                progress=False,
                # We're already inside asyncio.to_thread; disable yfinance's
                # own threading to avoid nested pools fighting each other.
                threads=False,
                auto_adjust=False,
                group_by="ticker",
                # `download` has no ``raise_errors`` argument: it catches each
                # ticker's failure itself and returns an empty frame for it.
                # A rate limit it does raise is re-raised below; the ones it
                # swallows only reach the caller through its log, which the
                # handler around this call captures.
            )
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError(
                "Yahoo Finance rate-limited the batch price download"
            )
        except Exception as e:  # pragma: no cover (upstream flakiness)
            logger.warning("yfinance batch download failed for %d symbols: %s", len(symbols), e)
            return out, capture.rate_limited
        finally:
            yf_logger.removeHandler(capture)

        rate_limited = capture.rate_limited
        if df is None or df.empty:
            return out, rate_limited

        # With group_by="ticker" the columns are a (sym, field) MultiIndex,
        # for a single ticker too (``multi_level_index`` defaults to True).
        # Flat columns are still read, for a yfinance that drops the level.
        if not isinstance(df.columns, pd.MultiIndex):
            if len(symbols) == 1 and "Close" in df.columns:
                out[symbols[0]] = _last_decimal_close(df["Close"])
            return out, rate_limited

        level0 = set(df.columns.get_level_values(0))
        for sym in symbols:
            if sym not in level0:
                continue
            try:
                sym_df = df[sym]
            except KeyError:
                continue
            close_series = sym_df.get("Close") if "Close" in getattr(sym_df, "columns", []) else None
            out[sym] = _last_decimal_close(close_series)
        return out, rate_limited

    @staticmethod
    def _price_sync(symbol: str) -> Optional[dict]:
        """The price half of a quote, from ``fast_info`` (cached short)."""
        import yfinance as yf

        try:
            fast = yf.Ticker(symbol).fast_info

            # FastInfo's public API is camelCase (snake_case is tolerated but
            # not advertised). See yfinance/scrapers/quote.py FastInfo class.
            price = _fast_info_value(fast, "lastPrice")
            # `lastPrice` is None briefly at market open; fall back to the
            # previous close so the UI still surfaces *something* sensible.
            if price is None:
                price = _fast_info_value(fast, "previousClose")
            quote_type = _fast_info_value(fast, "quoteType")
            currency = _fast_info_value(fast, "currency")
            exchange = _fast_info_value(fast, "exchange")
            return {
                "currency": str(currency) if currency else None,
                "exchange": str(exchange) if exchange else None,
                "price": float(price) if price is not None else None,
                "quote_type": str(quote_type).upper() if quote_type else None,
            }
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError(
                f"Yahoo Finance rate-limited while quoting {symbol}"
            )
        except Exception as e:  # pragma: no cover
            logger.warning("yfinance quote failed for %s: %s", symbol, e)
            return None

    @staticmethod
    def _info_sync(symbol: str) -> Optional[dict]:
        """The display half of a quote, from ``info`` (cached long).

        ``info`` is the slow path (full quoteSummary fetch) and the only
        place the display name lives. A failure returns None, which the
        caller treats as "no name" and does not cache.
        """
        import yfinance as yf

        try:
            info = yf.Ticker(symbol).info or {}
            # Everything below reads a third-party payload, so it stays inside
            # the handler: a malformed field must not fail a quote whose price
            # is already in hand.
            # First *usable* name wins: a wrongly typed `longName` must not
            # keep a valid `shortName` out, or the day-long cache keeps a
            # missing name for a symbol that did answer.
            name = next(
                (
                    value
                    for value in (info.get("longName"), info.get("shortName"))
                    if isinstance(value, str) and value
                ),
                None,
            )
            quote_type = info.get("quoteType")
            website = info.get("website") or None
            metadata = {
                "name": name,
                "quote_type": (
                    quote_type.upper()
                    if isinstance(quote_type, str) and quote_type
                    else None
                ),
                # Yahoo's `info["website"]` is the source of truth for the
                # company domain, from which we build the logo URL. Missing
                # for most crypto/indices — caller will just skip the logo.
                "website": website if isinstance(website, str) else None,
            }
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError(
                f"Yahoo Finance rate-limited while reading {symbol}"
            )
        except Exception as e:
            logger.warning("yfinance info failed for %s: %s", symbol, e)
            return None
        if not any(metadata.values()):
            # yfinance hides a failed quoteSummary by returning an `info` with
            # nothing usable (e.g. {"trailingPegRatio": None}). That is a
            # failure, not metadata: return None so the day-long cache stays
            # out of it and the next request tries again.
            logger.warning("yfinance info for %s had no usable fields", symbol)
            return None
        return metadata


# Asset logos reuse the shared favicon helper. Kept as a module-local alias so
# existing call sites stay untouched.
_logo_url_for = favicon_url_for


def _last_decimal_close(series) -> Optional[LatestClose]:
    """Return the most recent non-NaN Close price from a pandas Series.

    Weekends and holidays are NaN; drop them and grab the tail. If the
    whole series is empty or all-NaN we return None so the caller can
    skip the asset instead of writing garbage. The row's index is the
    trading day; a row without a readable date comes back with ``on=None``.
    """
    if series is None:
        return None
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        return None
    if len(series) == 0:
        return None
    cleaned = series.dropna() if hasattr(series, "dropna") else series
    if len(cleaned) == 0:
        return None
    val = cleaned.iloc[-1]
    if pd.isna(val):
        return None
    try:
        price = Decimal(str(float(val)))
    except (TypeError, ValueError):
        return None
    return LatestClose(price, _trading_day(cleaned.index[-1]))


def _trading_day(label) -> Optional[date]:
    """The calendar day of a ``yf.download`` row label (a pandas Timestamp)."""
    if isinstance(label, datetime):
        # A Timestamp is a datetime; its .date() is the exchange-local day.
        return label.date()
    if isinstance(label, date):
        return label
    return None


def _fast_info_value(fast_info, key: str):
    """Read a field from yfinance's FastInfo (dict-like or attribute-like).

    camelCase is the canonical key. snake_case is supported as a hidden alias
    in current versions, but we pass camelCase everywhere for forward safety.
    """
    if fast_info is None:
        return None
    try:
        value = fast_info[key]
    except (KeyError, TypeError):
        value = None
    if value is not None:
        return value
    # Attribute fallback covers versions where FastInfo was attribute-only.
    return getattr(fast_info, key, None)


_RATE_LIMIT_LOG_MARKERS = (
    "too many requests",
    "rate limited",
    "yfratelimiterror",
)


class _BatchRateLimitCapture(logging.Handler):
    """Hear the failures ``yf.download`` records instead of raising.

    yfinance 1.8.0 keeps each ticker's failure inside the download call's own
    context (``multi._DownloadCtx.errors``), which the caller cannot reach, and
    only writes them to the ``yfinance`` logger at ERROR ("N Failed downloads:").
    A 429 it swallowed for one ticker is therefore invisible in the returned
    frame, and it can sit next to tickers that answered. Its log is the only
    surface saying the batch was refused, whatever a later probe finds.

    Another call's failure can land here too, since the logger is shared; an
    unrelated 429 means the cooldown should start for everyone anyway.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.rate_limited = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage().lower()
        except Exception:  # pragma: no cover (a record we can't read isn't ours)
            return
        if any(marker in message for marker in _RATE_LIMIT_LOG_MARKERS):
            self.rate_limited = True


def _rate_limit_exception_types() -> tuple[type[BaseException], ...]:
    """Return the yfinance exception classes that signal upstream throttling.

    Resolved at call time because yfinance's exception module moved around
    between minor versions — we don't want an import error at boot if the
    user has a slightly older release.
    """
    types: list[type[BaseException]] = []
    try:
        from yfinance.exceptions import YFRateLimitError

        types.append(YFRateLimitError)
    except Exception:
        pass
    return tuple(types) or (  # fallback: never match, but keep the except branch valid
        _UnreachableException,
    )


class _UnreachableException(Exception):
    """Placeholder used when yfinance doesn't expose its rate-limit exception."""



class CompositeMarketPriceProvider(MarketPriceProvider):
    """Market-price provider that routes provider-specific symbols.

    Yahoo remains the default for ordinary tickers. Tesouro Direto bonds are
    exposed as compact TD:* symbols so they reuse the same market_price asset
    ledger, creation, refresh, and UI flows.
    """

    name = "composite"

    def __init__(self, default_provider: Optional[MarketPriceProvider] = None) -> None:
        self.default_provider = default_provider or YFinanceProvider()

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        q = (query or "").strip()
        # A Tesouro search wants bonds, not Yahoo's "tesouro" text matches — so
        # when we have bond results, return them alone. Fall through to Yahoo
        # only when Tesouro is off or has nothing (e.g. flag disabled).
        if _tesouro_enabled() and _looks_like_tesouro_query(q):
            tesouro = await self._search_tesouro(q, limit=limit)
            if tesouro:
                return tesouro[:limit]
        return await self.default_provider.search(q, limit=limit)

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        if _is_tesouro_symbol(symbol):
            return await self._tesouro_quote(symbol)
        return await self.default_provider.get_quote(symbol)

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        out: dict[str, Optional[Decimal]] = {}
        regular: list[str] = []
        tesouro: list[str] = []
        for symbol in symbols:
            if _is_tesouro_symbol(symbol):
                tesouro.append(symbol)
            else:
                regular.append(symbol)
        if regular:
            out.update(await self.default_provider.get_latest_prices(regular))
        if tesouro:
            out.update(await self._tesouro_latest_prices(tesouro))
        return out

    async def get_latest_closes(self, symbols: list[str]) -> dict[str, Optional[LatestClose]]:
        tesouro = [s for s in symbols if _is_tesouro_symbol(s)]
        regular = [s for s in symbols if not _is_tesouro_symbol(s)]
        out: dict[str, Optional[LatestClose]] = {}
        if regular:
            out.update(await self.default_provider.get_latest_closes(regular))
        if tesouro:
            prices = await self._tesouro_latest_prices(tesouro)
            out.update({s: LatestClose(p, None) if p is not None else None for s, p in prices.items()})
        return out

    async def _search_tesouro(self, query: str, limit: int) -> list[MarketSymbolMatch]:
        from app.providers.tesouro_direto import (
            _normalize,
            get_tesouro_direto_provider,
            tesouro_symbol_for,
        )

        quotes = await get_tesouro_direto_provider().get_available_bonds()
        # Filter by name so the shared search box behaves like ticker search:
        # "selic" → Selic bonds, "ipca 2035" → that maturity. "tesouro"/"direto"
        # are dropped as filler so a bare "tesouro" lists everything.
        tokens = [t for t in _normalize(query).split() if t and t not in ("tesouro", "direto")]
        if tokens:
            quotes = [
                q
                for q in quotes
                if all(tok in _normalize(f"{q.title_type} {q.maturity_date.year}") for tok in tokens)
            ]
        return [
            MarketSymbolMatch(
                symbol=tesouro_symbol_for(q.title_type, q.maturity_date),
                name=f"{q.title_type} · {q.maturity_date.strftime('%d/%m/%Y')}",
                exchange="Tesouro Direto",
                quote_type="BOND",
            )
            for q in quotes[:limit]
        ]

    async def _tesouro_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        from app.providers.tesouro_direto import get_tesouro_direto_provider, tesouro_symbol_for

        quote = await get_tesouro_direto_provider().get_quote_by_symbol(symbol)
        if quote is None:
            return None
        return MarketSymbolQuote(
            symbol=tesouro_symbol_for(quote.title_type, quote.maturity_date),
            name=f"{quote.title_type} {quote.maturity_date.year}",
            exchange="Tesouro Direto",
            currency="BRL",
            price=float(quote.pu_base),
            quote_type="BOND",
        )

    async def _tesouro_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        from app.providers.tesouro_direto import get_tesouro_direto_provider

        quotes = await get_tesouro_direto_provider().get_quotes_by_symbol(symbols)
        return {symbol.upper(): (q.pu_base if q else None) for symbol, q in quotes.items()}

def _tesouro_enabled() -> bool:
    try:
        from app.core.config import get_settings

        return bool(get_settings().tesouro_direto_enabled)
    except Exception:
        return False

# Bond-name keywords that route the shared search to Tesouro Direto. A bare
# "td" prefix is deliberately excluded — it collides with real tickers like TD
# (Toronto-Dominion Bank). Stock/crypto queries skip the bond path entirely so
# they never wait on the Treasury CSV.
_TESOURO_KEYWORDS = ("tesouro", "selic", "ipca", "prefixado", "igpm", "educa", "renda")


def _looks_like_tesouro_query(query: str) -> bool:
    normalized = query.strip().casefold()
    return any(keyword in normalized for keyword in _TESOURO_KEYWORDS)

def _is_tesouro_symbol(symbol: str | None) -> bool:
    try:
        from app.providers.tesouro_direto import is_tesouro_symbol

        return is_tesouro_symbol(symbol)
    except Exception:
        return False

# Module-level singleton — cheap to construct, stateless beyond the yfinance
# import, and consumers need a stable instance for dependency overrides.
_default_provider: Optional[MarketPriceProvider] = None


def get_market_price_provider() -> MarketPriceProvider:
    """Return the configured market-price provider (yfinance by default)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = CompositeMarketPriceProvider()
    return _default_provider


def set_market_price_provider(provider: Optional[MarketPriceProvider]) -> None:
    """Test helper — swap the singleton for a fake."""
    global _default_provider
    _default_provider = provider
