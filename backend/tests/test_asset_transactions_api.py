"""API-level tests for the investment transaction ledger (issue #235).

Exercises the HTTP surface: per-asset CRUD, the workspace-wide list + filters,
the find-or-create buy endpoint, validation, 404s and auth.
"""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.user import User


@pytest_asyncio.fixture
async def market_asset_api(session: AsyncSession, test_user: User) -> Asset:
    """A market-priced holding with a cached last price (no provider needed)."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Petrobras",
        type="stock",
        currency="BRL",
        valuation_method="market_price",
        ticker="PETR4.SA",
        last_price=Decimal("30.00"),
        position=0,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_add_transaction_via_api(client: AsyncClient, auth_headers: dict, market_asset_api: Asset):
    resp = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["units"] == 10
    assert round(body["average_price"], 2) == 20.00
    assert body["transaction_count"] == 1


@pytest.mark.asyncio
async def test_transaction_list_and_filters(client: AsyncClient, auth_headers: dict, market_asset_api: Asset):
    for tx in [
        {"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
        {"kind": "sell", "quantity": 4, "price": 30, "date": "2026-03-01"},
    ]:
        r = await client.post(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers, json=tx)
        assert r.status_code == 201

    # Per-asset list
    r = await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2

    # Workspace-wide list
    r = await client.get("/api/assets/transactions", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["ticker"] == "PETR4.SA" for row in rows)

    # Filter by kind
    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"kind": "sell"})
    assert [row["kind"] for row in r.json()] == ["sell"]

    # Filter by ticker
    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"ticker": "PETR4.SA"})
    assert len(r.json()) == 2
    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"ticker": "NOPE"})
    assert r.json() == []


@pytest.mark.asyncio
async def test_update_and_delete_transaction_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    r = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )
    assert r.status_code == 201
    tx_id = (await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)).json()[0]["id"]

    r = await client.patch(f"/api/assets/transactions/{tx_id}", headers=auth_headers, json={"quantity": 25})
    assert r.status_code == 200
    assert r.json()["units"] == 25

    r = await client.delete(f"/api/assets/transactions/{tx_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["units"] == 0
    assert r.json()["average_price"] is None


@pytest.mark.asyncio
async def test_buy_consolidates_existing_ticker_via_api(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, market_asset_api: Asset
):
    # First seed a buy so the holding has a position.
    await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )
    # /assets/buy on the SAME ticker hits the find path (no provider call needed).
    r = await client.post(
        "/api/assets/buy",
        headers=auth_headers,
        json={"ticker": "PETR4.SA", "quantity": 10, "price": 30, "date": "2026-02-01"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == str(market_asset_api.id)  # consolidated, not a new asset
    assert body["units"] == 20
    assert round(body["average_price"], 2) == 25.00


@pytest.mark.asyncio
async def test_add_transaction_unknown_asset_404(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        f"/api/assets/{uuid.uuid4()}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 1, "price": 1, "date": "2026-01-01"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_transaction_invalid_kind_422(client: AsyncClient, auth_headers: dict, market_asset_api: Asset):
    r = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "gift", "quantity": 1, "price": 1, "date": "2026-01-01"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_transaction_zero_quantity_422(client: AsyncClient, auth_headers: dict, market_asset_api: Asset):
    r = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 0, "price": 10, "date": "2026-01-01"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_oversell_rejected_via_api(client: AsyncClient, auth_headers: dict, market_asset_api: Asset):
    await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )
    r = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "sell", "quantity": 11, "price": 30, "date": "2026-02-01"},
    )
    assert r.status_code == 422
    assert "only" in r.json()["detail"].lower()
    # Rejected sell left no trace — still just the one buy.
    r2 = await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_transactions_require_auth(client: AsyncClient):
    assert (await client.get("/api/assets/transactions")).status_code == 401
