"""Market-price data providers (stocks, ETFs, crypto, funds).

Asset valuation from a live public quote — the user enters a ticker and
quantity, the provider returns the price, and Securo tracks the value
over time. This is deliberately separate from `BankProvider`: there's no
per-user authentication and no accounts/transactions, just a symbol
lookup backed by a public data source (Yahoo Finance today).

Why a thin abstraction: swapping Yahoo out (for Stooq, Alpha Vantage,
CoinGecko, or a user-chosen source) is a realistic future, and keeping
the asset service off the concrete provider makes those swaps non-events.

yfinance usage notes (matches v1.3.x):
  * ``yfinance.Search(query, max_results=..., news_count=..., enable_fuzzy_query=...)``
    returns a ``.quotes`` list of dicts with keys like ``symbol``, ``shortname``,
    ``longname``, ``exchange``, ``exchDisp``, ``quoteType``.
  * ``yfinance.Ticker(symbol).fast_info`` exposes camelCase keys —
    ``lastPrice``, ``previousClose``, ``currency``, ``exchange``, ``quoteType``.
    snake_case keys are silently accepted too, but camelCase is the public API.
  * ``yf.config.network.retries`` enables transient-error retries (exponential
    backoff). Set once at module import so both Search and Ticker benefit.
  * ``YFRateLimitError`` is raised when Yahoo rate-limits us — we surface it
    so the caller can decide (UI toast vs. scheduled-task skip).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from app.providers.favicon import favicon_url_for
from app.schemas.asset import MarketSymbolMatch, MarketSymbolQuote

logger = logging.getLogger(__name__)


# Yahoo Finance occasionally reports prices in minor units (pence, cents).
# These are not valid ISO 4217 codes and, if kept raw, produce 100x-wrong
# valuations. Same table Sure uses (see app/models/provider/yahoo_finance.rb).
_MINOR_UNIT_CURRENCIES: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),  # British pence → pounds (e.g. IITU.L)
    "ZAc": ("ZAR", 0.01),  # South African cents → rand (e.g. JSE.JO)
}


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
        raw = await asyncio.to_thread(self._search_sync, q, limit)
        matches: list[MarketSymbolMatch] = []
        for quote in raw:
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

    # Practical cap per `yf.download` request. Yahoo accepts more but starts
    # silently dropping symbols beyond ~100 in community reports — we chunk
    # above this to keep the success rate high.
    _BATCH_CHUNK_SIZE = 100

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        """One HTTP request per ~100 tickers via ``yfinance.download``.

        Much cheaper than looping ``get_quote`` for a scheduled refresh — a
        portfolio of 50 market-priced assets goes from 50 calls to 1. The
        tradeoff: ``download`` returns prices only (no currency/name/etc.).
        That's fine here because ``Asset.currency`` is already cached from
        creation time; we just update the price and let ``current_value``
        recompute from ``units × last_price``.

        Note on minor-unit currencies (GBp, ZAc on LSE/JSE): those are
        normalized to GBP/ZAR ×0.01 at creation (via ``get_quote``), but
        on refresh we only get the raw trading-currency close. The batch
        preserves the same unit as the upstream quote, so internal
        consistency is maintained as long as the asset's stored currency
        matches the ticker's reporting unit. If Yahoo changes a listing's
        reporting unit mid-stream the price will appear off by 100× — rare
        enough to address reactively rather than pre-emptively.
        """
        if not symbols:
            return {}
        unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        if not unique:
            return {}

        out: dict[str, Optional[Decimal]] = {s: None for s in unique}
        for i in range(0, len(unique), self._BATCH_CHUNK_SIZE):
            chunk = unique[i : i + self._BATCH_CHUNK_SIZE]
            try:
                chunk_prices = await asyncio.to_thread(self._download_prices_sync, chunk)
            except _rate_limit_exception_types():
                raise MarketPriceRateLimitedError(
                    "Yahoo Finance rate-limited the batch price download"
                )
            out.update(chunk_prices)
        return out

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        raw = await asyncio.to_thread(self._quote_sync, sym)
        if raw is None:
            return None
        price = raw.get("price")
        currency = raw.get("currency")
        if price is None or not currency:
            return None
        normalized_currency, normalized_price = _normalize_currency_and_price(
            currency, float(price)
        )
        return MarketSymbolQuote(
            symbol=sym,
            name=raw.get("name"),
            exchange=raw.get("exchange"),
            currency=normalized_currency,
            price=normalized_price,
            quote_type=raw.get("quote_type"),
            logo_url=_logo_url_for(raw.get("website")),
        )

    # ---- sync helpers (called via asyncio.to_thread) ----

    @staticmethod
    def _search_sync(query: str, limit: int) -> list[dict]:
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
            if isinstance(quotes, list):
                return quotes
            # Defensive fallback for any future shape drift.
            response = getattr(result, "response", None) or {}
            return list(response.get("quotes") or [])
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError("Yahoo Finance rate-limited the search")
        except Exception as e:  # pragma: no cover — upstream flakiness, not logic
            logger.warning("yfinance search failed for %r: %s", query, e)
            return []

    @staticmethod
    def _download_prices_sync(symbols: list[str]) -> dict[str, Optional[Decimal]]:
        """Run ``yf.download`` and extract last-close per symbol.

        ``yf.download`` returns a pandas DataFrame: flat columns for a
        single symbol, MultiIndex (symbol, field) for multiple. Period is
        ``"5d"`` so we span weekends and market holidays while still
        getting today's close once the market has ticked.
        """
        import yfinance as yf

        out: dict[str, Optional[Decimal]] = {s: None for s in symbols}
        if not symbols:
            return out

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
                # raise_errors keeps us in control of exception handling.
                # Swallowing upstream failures silently would leave callers
                # unable to distinguish "no data" from "request blew up".
            )
        except Exception as e:  # pragma: no cover — upstream flakiness
            logger.warning("yfinance batch download failed for %d symbols: %s", len(symbols), e)
            return out

        if df is None or df.empty:
            return out

        # Single-ticker downloads come back with flat columns — no MultiIndex.
        # Multi-ticker downloads use group_by="ticker" → MultiIndex (sym, field).
        if len(symbols) == 1:
            close_series = df.get("Close") if "Close" in df.columns else None
            out[symbols[0]] = _last_decimal_close(close_series)
            return out

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
        return out

    @staticmethod
    def _quote_sync(symbol: str) -> Optional[dict]:
        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            fast = ticker.fast_info

            # FastInfo's public API is camelCase (snake_case is tolerated but
            # not advertised). See yfinance/scrapers/quote.py FastInfo class.
            price = _fast_info_value(fast, "lastPrice")
            # `lastPrice` is None briefly at market open; fall back to the
            # previous close so the UI still surfaces *something* sensible.
            if price is None:
                price = _fast_info_value(fast, "previousClose")
            currency = _fast_info_value(fast, "currency")
            exchange = _fast_info_value(fast, "exchange")
            quote_type = _fast_info_value(fast, "quoteType")

            # `info` is the slow path (full quoteSummary fetch) and the only
            # place the display name lives. Swallow any failure — the caller
            # only truly needs price + currency.
            try:
                info = ticker.info or {}
            except Exception:
                info = {}
            name = info.get("longName") or info.get("shortName") or None
            if not quote_type:
                quote_type = (info.get("quoteType") or "").upper() or None
            else:
                quote_type = str(quote_type).upper()

            return {
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "currency": currency,
                "price": float(price) if price is not None else None,
                "quote_type": quote_type,
                # Yahoo's `info["website"]` is the source of truth for the
                # company domain, from which we build the logo URL. Missing
                # for most crypto/indices — caller will just skip the logo.
                "website": info.get("website") or None,
            }
        except _rate_limit_exception_types():
            raise MarketPriceRateLimitedError(
                f"Yahoo Finance rate-limited while quoting {symbol}"
            )
        except Exception as e:  # pragma: no cover
            logger.warning("yfinance quote failed for %s: %s", symbol, e)
            return None


# Asset logos reuse the shared favicon helper. Kept as a module-local alias so
# existing call sites stay untouched.
_logo_url_for = favicon_url_for


def _last_decimal_close(series) -> Optional[Decimal]:
    """Return the most recent non-NaN Close price from a pandas Series.

    Weekends and holidays are NaN; drop them and grab the tail. If the
    whole series is empty or all-NaN we return None so the caller can
    skip the asset instead of writing garbage.
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
        return Decimal(str(float(val)))
    except (TypeError, ValueError):
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


# Module-level singleton — cheap to construct, stateless beyond the yfinance
# import, and consumers need a stable instance for dependency overrides.
_default_provider: Optional[MarketPriceProvider] = None


def get_market_price_provider() -> MarketPriceProvider:
    """Return the configured market-price provider (yfinance by default)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = YFinanceProvider()
    return _default_provider


def set_market_price_provider(provider: Optional[MarketPriceProvider]) -> None:
    """Test helper — swap the singleton for a fake."""
    global _default_provider
    _default_provider = provider
