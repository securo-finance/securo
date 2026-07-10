from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
import requests

TESOURO_DIRETO_CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
)

TESOURO_SYMBOL_PREFIX = "TD"

# The official CSV carries years of daily history for every bond, so a fresh
# download + parse takes ~25s. Prices only change once per business day, so we
# cache the latest-per-bond snapshot in-process for several hours — long enough
# that as-you-type search stays warm through a session. Shared across provider
# instances since get_tesouro_direto_provider() returns a fresh one each call.
_CACHE_TTL_SECONDS = 6 * 60 * 60
_latest_cache: dict = {"ts": 0.0, "quotes": None}


def tesouro_symbol_for(title_type: str, maturity_date: date) -> str:
    """Return a compact market-price symbol for a Tesouro Direto bond.

    Asset.ticker is limited to 32 chars, so we cannot store the full bond
    title. The short hash is derived from the normalized title and resolved
    against the official CSV together with maturity date.
    """
    digest = hashlib.sha1(_normalize(title_type).encode("utf-8")).hexdigest()[:8].upper()
    return f"{TESOURO_SYMBOL_PREFIX}:{digest}:{maturity_date.isoformat()}"


def is_tesouro_symbol(symbol: str | None) -> bool:
    return bool(symbol and symbol.upper().startswith(f"{TESOURO_SYMBOL_PREFIX}:"))


def parse_tesouro_symbol(symbol: str) -> tuple[str, date]:
    parts = (symbol or "").strip().upper().split(":", 2)
    if len(parts) != 3 or parts[0] != TESOURO_SYMBOL_PREFIX or not parts[1]:
        raise ValueError(f"invalid Tesouro Direto symbol: {symbol}")
    return parts[1], _parse_date(parts[2])


@dataclass(frozen=True)
class TesouroDiretoQuote:
    title_type: str
    maturity_date: date
    price_date: date
    pu_base: Decimal


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize(value))


def parse_brl_decimal(value: str) -> Decimal:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty decimal")
    # Treasury CSV usually uses pt-BR format (15.130,10). Plain decimal is
    # accepted for tests and future endpoint changes.
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _parse_date(value: str) -> date:
    raw = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date: {value}")


class TesouroDiretoProvider:
    """Official Tesouro Transparente CSV provider.

    Tesouro Direto bonds do not have stable tickers, so prices are keyed by the
    pair (title type, maturity date). The official CSV includes historical rows;
    we keep the latest Data Base for each key.
    """

    def __init__(self, *, csv_text: str | None = None, url: str = TESOURO_DIRETO_CSV_URL) -> None:
        self._csv_text = csv_text
        self.url = url

    async def get_latest_price(
        self, title_type: str, maturity_date: date
    ) -> Optional[TesouroDiretoQuote]:
        target = (_normalize(title_type), maturity_date)
        for quote in await self._latest_quotes():
            if (_normalize(quote.title_type), quote.maturity_date) == target:
                return quote
        return None

    async def get_latest_prices(
        self, keys: list[tuple[str, date]]
    ) -> dict[tuple[str, date], Optional[TesouroDiretoQuote]]:
        wanted = {(_normalize(t), m): (t, m) for t, m in keys}
        out: dict[tuple[str, date], Optional[TesouroDiretoQuote]] = {key: None for key in keys}
        for quote in await self._latest_quotes():
            original = wanted.get((_normalize(quote.title_type), quote.maturity_date))
            if original is not None:
                out[original] = quote
        return out

    async def get_available_bonds(self) -> list[TesouroDiretoQuote]:
        # Only bonds still open for investment — the CSV also lists long-matured
        # series that nobody should be adding as a current holding.
        today = date.today()
        bonds = [q for q in await self._latest_quotes() if q.maturity_date >= today]
        return sorted(bonds, key=lambda q: (q.title_type, q.maturity_date))

    async def get_quote_by_symbol(self, symbol: str) -> Optional[TesouroDiretoQuote]:
        try:
            title_hash, maturity_date = parse_tesouro_symbol(symbol)
        except ValueError:
            return None
        for quote in await self._latest_quotes():
            if quote.maturity_date != maturity_date:
                continue
            if (
                tesouro_symbol_for(quote.title_type, quote.maturity_date).split(":")[1]
                == title_hash
            ):
                return quote
        return None

    async def get_quotes_by_symbol(
        self, symbols: list[str]
    ) -> dict[str, Optional[TesouroDiretoQuote]]:
        return {symbol.upper(): await self.get_quote_by_symbol(symbol) for symbol in symbols}

    async def _latest_quotes(self) -> list[TesouroDiretoQuote]:
        """Latest (most recent Data Base) quote per (title, maturity).

        Cached in-process for ``_CACHE_TTL_SECONDS`` to avoid re-downloading and
        re-parsing the full historical CSV on every quote/search/refresh.
        """
        if self._csv_text is not None:
            return self._build_latest(self._csv_text)
        now = time.monotonic()
        cached = _latest_cache["quotes"]
        if cached is not None and (now - _latest_cache["ts"]) < _CACHE_TTL_SECONDS:
            return cached
        csv_text = await asyncio.to_thread(self._download_csv)
        quotes = self._build_latest(csv_text)
        _latest_cache["quotes"] = quotes
        _latest_cache["ts"] = now
        return quotes

    def _build_latest(self, csv_text: str) -> list[TesouroDiretoQuote]:
        latest: dict[tuple[str, date], TesouroDiretoQuote] = {}
        for quote in self._iter_quotes(csv_text):
            key = (_normalize(quote.title_type), quote.maturity_date)
            if key not in latest or quote.price_date > latest[key].price_date:
                latest[key] = quote
        return list(latest.values())

    def find_price(
        self,
        title_type: str,
        maturity_date: date,
        *,
        csv_text: str | None = None,
    ) -> Optional[TesouroDiretoQuote]:
        target = (_normalize(title_type), maturity_date)
        latest: Optional[TesouroDiretoQuote] = None
        for quote in self._iter_quotes(csv_text or self._csv_text or ""):
            if (_normalize(quote.title_type), quote.maturity_date) != target:
                continue
            if latest is None or quote.price_date > latest.price_date:
                latest = quote
        return latest

    def find_price_by_symbol(
        self,
        symbol: str,
        *,
        csv_text: str | None = None,
    ) -> Optional[TesouroDiretoQuote]:
        try:
            title_hash, maturity_date = parse_tesouro_symbol(symbol)
        except ValueError:
            return None
        latest: Optional[TesouroDiretoQuote] = None
        for quote in self._iter_quotes(csv_text or self._csv_text or ""):
            if quote.maturity_date != maturity_date:
                continue
            if (
                tesouro_symbol_for(quote.title_type, quote.maturity_date).split(":")[1]
                != title_hash
            ):
                continue
            if latest is None or quote.price_date > latest.price_date:
                latest = quote
        return latest

    def list_latest_quotes(self, *, csv_text: str | None = None) -> list[TesouroDiretoQuote]:
        latest: dict[tuple[str, date], TesouroDiretoQuote] = {}
        for quote in self._iter_quotes(csv_text or self._csv_text or ""):
            key = (_normalize(quote.title_type), quote.maturity_date)
            if key not in latest or quote.price_date > latest[key].price_date:
                latest[key] = quote
        return sorted(latest.values(), key=lambda q: (q.title_type, q.maturity_date))

    def _download_csv(self) -> str:
        response = requests.get(
            self.url,
            headers={"User-Agent": "Securo TesouroDiretoProvider"},
            timeout=30,
        )
        response.raise_for_status()
        response.encoding = response.encoding or "latin1"
        return response.text

    def _iter_quotes(self, csv_text: str):
        if not csv_text.strip():
            return
        sample = csv_text[:2048]
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
        if not reader.fieldnames:
            return
        header_map = {_header_key(name): name for name in reader.fieldnames}
        title_col = header_map.get("tipotitulo")
        maturity_col = header_map.get("datavencimento")
        price_date_col = header_map.get("database")
        pu_col = (
            header_map.get("pubasem\u00e3ha")
            or header_map.get("pubasemanh")
            or header_map.get("pubase")
        )
        # _header_key removes accents, so the real key is pubasemanh[a]. Keep a
        # fallback scan to avoid coupling to exact spelling.
        if not pu_col:
            for key, original in header_map.items():
                if key.startswith("pubase"):
                    pu_col = original
                    break
        if not (title_col and maturity_col and price_date_col and pu_col):
            return
        for row in reader:
            try:
                yield TesouroDiretoQuote(
                    title_type=(row.get(title_col) or "").strip(),
                    maturity_date=_parse_date(row.get(maturity_col) or ""),
                    price_date=_parse_date(row.get(price_date_col) or ""),
                    pu_base=parse_brl_decimal(row.get(pu_col) or ""),
                )
            except ValueError:
                continue


def get_tesouro_direto_provider() -> TesouroDiretoProvider:
    return TesouroDiretoProvider()
