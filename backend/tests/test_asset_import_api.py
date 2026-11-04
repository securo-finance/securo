"""HTTP surface of the asset-order importer: template, preview, commit, auth."""
import json
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.providers.market_price import (
    MarketPriceProvider,
    MarketSymbolQuote,
    set_market_price_provider,
)


class StubProvider(MarketPriceProvider):
    name = "stub"

    async def search(self, query: str, limit: int = 20):
        return []

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        if symbol.upper() != "AAPL":
            return None
        return MarketSymbolQuote(
            symbol="AAPL", name="Apple Inc", exchange="NASDAQ",
            currency="USD", price=180.0, quote_type="EQUITY",
        )

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        return {s.upper(): (Decimal("180") if s.upper() == "AAPL" else None) for s in symbols}


@pytest_asyncio.fixture(autouse=True)
async def stub_provider():
    set_market_price_provider(StubProvider())
    yield
    set_market_price_provider(None)


CSV = b"ticker,date,quantity,price,fee\nAAPL,2026-01-15,10,150.00,1.20\nAAPL,2026-02-15,-4,180.00,1.20\n"


@pytest.mark.asyncio
async def test_template_is_downloadable(client: AsyncClient, auth_headers):
    resp = await client.get("/api/assets/import/template", headers=auth_headers)
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0].startswith("ticker*,date*,quantity*,price*")


@pytest.mark.asyncio
async def test_preview_reports_what_the_import_would_do(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", CSV, "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["orders"]) == 2
    assert body["holdings_created"] == 1
    assert body["errors"] == []
    assert body["csv_columns"] == ["ticker", "date", "quantity", "price", "fee"]


@pytest.mark.asyncio
async def test_preview_writes_nothing(client: AsyncClient, auth_headers):
    await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", CSV, "text/csv")},
        headers=auth_headers,
    )
    assets = await client.get("/api/assets", headers=auth_headers)
    assert assets.json() == []


@pytest.mark.asyncio
async def test_unmappable_file_returns_the_headers_for_the_mapping_step(
    client: AsyncClient, auth_headers
):
    """A soft failure, like the transaction preview: the UI needs the columns."""
    resp = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", b"col_a,col_b\nAAPL,10\n", "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parse_error"]
    assert body["csv_columns"] == ["col_a", "col_b"]
    assert body["orders"] == []


@pytest.mark.asyncio
async def test_preview_honours_an_explicit_mapping(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", b"a,b,c,d\nAAPL,2026-01-15,10,150.00\n", "text/csv")},
        data={"column_mapping": json.dumps({"ticker": "a", "date": "b", "quantity": "c", "price": "d"})},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["orders"]) == 1


@pytest.mark.asyncio
async def test_import_creates_the_holding(client: AsyncClient, auth_headers):
    preview = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", CSV, "text/csv")},
        headers=auth_headers,
    )
    resp = await client.post(
        "/api/assets/import",
        json={"orders": preview.json()["orders"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 2
    assert resp.json()["holdings_created"] == 1

    assets = (await client.get("/api/assets", headers=auth_headers)).json()
    assert [a["ticker"] for a in assets] == ["AAPL"]
    assert Decimal(str(assets[0]["units"])) == Decimal("6")  # 10 bought, 4 sold


@pytest.mark.asyncio
async def test_import_requires_authentication(client: AsyncClient):
    resp = await client.post("/api/assets/import", json={"orders": []})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preview_requires_authentication(client: AsyncClient):
    resp = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", CSV, "text/csv")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preview_lists_only_the_rows_it_will_import(client: AsyncClient, auth_headers):
    """A row the dry run rejects must not sit in the table above a button that
    promises to import it."""
    csv = b"ticker,date,quantity,price\nAAPL,2026-01-15,10,150.00\nNOSUCH,2026-01-16,5,10.00\n"
    resp = await client.post(
        "/api/assets/import/preview",
        files={"file": ("orders.csv", csv, "text/csv")},
        headers=auth_headers,
    )
    body = resp.json()
    assert [o["ticker"] for o in body["orders"]] == ["AAPL"]
    assert [(e["row"], e["reason"]) for e in body["errors"]] == [(3, "unknown_ticker")]
