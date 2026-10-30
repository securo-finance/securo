"""API-level tests for the investment transaction ledger (issue #235).

Exercises the HTTP surface: per-asset CRUD, the workspace-wide list + filters,
the find-or-create buy endpoint, validation, 404s and auth.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
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
async def test_deleting_a_split_a_later_sell_needs_returns_422(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    url = f"/api/assets/{market_asset_api.id}/transactions"
    for body in (
        {"kind": "buy", "quantity": 3, "price": 485, "date": "2026-01-01"},
        {"kind": "split", "old_units": 1, "new_units": 100, "date": "2026-02-01"},
        {"kind": "sell", "quantity": 100, "price": 6, "date": "2026-02-02"},
    ):
        assert (await client.post(url, headers=auth_headers, json=body)).status_code == 201
    ledger = (await client.get(url, headers=auth_headers)).json()
    split_id = next(tx["id"] for tx in ledger if tx["kind"] == "split")

    r = await client.delete(f"/api/assets/transactions/{split_id}", headers=auth_headers)

    assert r.status_code == 422
    assert "later sell" in r.json()["detail"]
    assert len((await client.get(url, headers=auth_headers)).json()) == 3


@pytest.mark.asyncio
async def test_a_split_too_large_to_store_returns_422(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    url = f"/api/assets/{market_asset_api.id}/transactions"
    buy = {"kind": "buy", "quantity": 1000, "price": 480, "date": "2026-01-01"}
    assert (await client.post(url, headers=auth_headers, json=buy)).status_code == 201

    r = await client.post(
        url,
        headers=auth_headers,
        json={"kind": "split", "old_units": 1, "new_units": 1_000_000, "date": "2026-02-01"},
    )

    assert r.status_code == 422
    assert "too large to store" in r.json()["detail"]
    assert len((await client.get(url, headers=auth_headers)).json()) == 1


@pytest.mark.asyncio
async def test_update_transaction_all_fields_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    r = await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )
    assert r.status_code == 201
    tx_id = (
        await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    ).json()[0]["id"]

    r = await client.patch(
        f"/api/assets/transactions/{tx_id}",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 119, "price": 43.47, "fee": 0, "date": "2025-04-15"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["units"] == 119
    assert round(body["average_price"], 2) == 43.47

    r = await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    tx = r.json()[0]
    assert tx["kind"] == "buy"
    assert tx["quantity"] == 119
    assert round(tx["price"], 2) == 43.47
    assert tx["fee"] == 0
    assert tx["date"] == "2025-04-15"


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


@pytest.mark.asyncio
async def test_editing_ledger_backed_holding_keeps_cost_basis(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    """Renaming a tracked holding must not wipe its ledger-derived position (issue #965)."""
    await client.post(
        f"/api/assets/{market_asset_api.id}/transactions",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 10, "price": 20, "date": "2026-01-01"},
    )

    # Same shape the edit dialog used to send: cleared cost and form units.
    resp = await client.patch(
        f"/api/assets/{market_asset_api.id}",
        headers=auth_headers,
        json={
            "name": "Petrobras PN",
            "purchase_price": None,
            "purchase_date": None,
            "sell_date": None,
            "sell_price": None,
            "units": 3,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Petrobras PN"
    assert body["units"] == 10
    assert body["purchase_price"] == 200
    assert body["total_invested"] == 200
    assert round(body["average_price"], 2) == 20.00
    assert body["purchase_date"] == "2026-01-01"
    assert body["transaction_count"] == 1

    listed = await client.get("/api/assets", headers=auth_headers)
    holding = next(a for a in listed.json() if a["id"] == str(market_asset_api.id))
    assert holding["total_invested"] == 200
    assert holding["units"] == 10


@pytest.mark.asyncio
async def test_editing_legacy_market_asset_without_ledger_updates_units(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    """A market holding with no transactions keeps its units editable."""
    resp = await client.patch(
        f"/api/assets/{market_asset_api.id}",
        headers=auth_headers,
        json={"units": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["units"] == 5
    assert body["average_price"] is None
    assert body["transaction_count"] == 0


# ---------------------------------------------------------------------------
# Stock splits (issue #944)
# ---------------------------------------------------------------------------


async def _post_split(client: AsyncClient, headers: dict, asset: Asset, **payload):
    return await client.post(
        f"/api/assets/{asset.id}/transactions",
        headers=headers,
        json={"kind": "split", "date": "2026-10-27", **payload},
    )


async def _seed_buy(client: AsyncClient, headers: dict, asset: Asset):
    r = await client.post(
        f"/api/assets/{asset.id}/transactions",
        headers=headers,
        json={"kind": "buy", "quantity": 10, "price": 480, "date": "2026-01-05"},
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_add_edit_and_list_a_split_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    await _seed_buy(client, auth_headers, market_asset_api)

    r = await _post_split(client, auth_headers, market_asset_api, old_units=1, new_units=100)
    assert r.status_code == 201
    body = r.json()
    assert body["units"] == 1000
    assert body["transaction_count"] == 2
    assert round(body["average_price"], 2) == 4.80
    assert body["total_invested"] == 4800  # the split moves no money

    listed = (
        await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    ).json()
    split = next(tx for tx in listed if tx["kind"] == "split")
    assert (split["old_units"], split["new_units"]) == (1, 100)
    assert (split["quantity"], split["price"], split["fee"]) == (0, 0, 0)
    assert split["date"] == "2026-10-27"
    buy = next(tx for tx in listed if tx["kind"] == "buy")
    assert (buy["old_units"], buy["new_units"]) == (None, None)

    r = await client.patch(
        f"/api/assets/transactions/{split['id']}",
        headers=auth_headers,
        json={"old_units": 1, "new_units": 10},
    )
    assert r.status_code == 200
    assert r.json()["units"] == 100


@pytest.mark.asyncio
async def test_transaction_list_filters_by_split(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    await _seed_buy(client, auth_headers, market_asset_api)
    await _post_split(client, auth_headers, market_asset_api, old_units=1, new_units=100)

    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"kind": "split"})
    assert r.status_code == 200
    assert [(row["kind"], row["old_units"], row["new_units"]) for row in r.json()] == [
        ("split", 1, 100)
    ]
    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"kind": "buy"})
    assert [row["kind"] for row in r.json()] == ["buy"]

    # Anything else is still refused.
    r = await client.get("/api/assets/transactions", headers=auth_headers, params={"kind": "gift"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "payload,detail",
    [
        ({"old_units": 5, "new_units": 5}, "old_units and new_units must differ"),
        ({"old_units": 1}, "old_units and new_units must be whole numbers from 1 to 1000000"),
        ({"old_units": 1, "new_units": 2, "quantity": 5}, "a split has no quantity, price or fee"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_split_is_rejected_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset, payload: dict, detail: str
):
    await _seed_buy(client, auth_headers, market_asset_api)

    r = await _post_split(client, auth_headers, market_asset_api, **payload)

    assert r.status_code == 422
    assert r.json()["detail"] == detail
    listed = await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_fractional_split_ratio_is_rejected_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    """The ratio is two whole numbers, so a 3-for-2 split is 2 → 3, not 1 → 1.5."""
    await _seed_buy(client, auth_headers, market_asset_api)

    r = await _post_split(client, auth_headers, market_asset_api, old_units=1, new_units=1.5)

    assert r.status_code == 422
    assert [error["loc"] for error in r.json()["detail"]] == [["body", "new_units"]]


@pytest.mark.asyncio
async def test_split_on_a_manual_holding_is_rejected_via_api(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    manual = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Flat", type="real_estate", currency="BRL",
        valuation_method="manual", position=0,
    )
    session.add(manual)
    await session.commit()
    await _seed_buy(client, auth_headers, manual)

    r = await _post_split(client, auth_headers, manual, old_units=1, new_units=100)

    assert r.status_code == 422
    assert r.json()["detail"] == (
        "Splits can only be recorded on market-priced holdings "
        "that aren't synced from a bank connection."
    )


@pytest.mark.asyncio
async def test_split_cannot_turn_into_a_buy_via_api(
    client: AsyncClient, auth_headers: dict, market_asset_api: Asset
):
    await _seed_buy(client, auth_headers, market_asset_api)
    await _post_split(client, auth_headers, market_asset_api, old_units=1, new_units=100)
    listed = (
        await client.get(f"/api/assets/{market_asset_api.id}/transactions", headers=auth_headers)
    ).json()
    split_id = next(tx["id"] for tx in listed if tx["kind"] == "split")

    r = await client.patch(
        f"/api/assets/transactions/{split_id}",
        headers=auth_headers,
        json={"kind": "buy", "quantity": 1, "price": 1},
    )

    assert r.status_code == 422
    assert r.json()["detail"].startswith("A split can't become a buy or a sell")


@pytest.mark.parametrize(
    "kind,old_units,new_units",
    [
        ("split", None, None),
        ("split", 1, None),
        ("split", None, 2),
        ("split", 0, 2),
        ("split", 1, -3),
        ("buy", 1, 2),
        ("sell", 1, None),
        ("sell", None, 2),
    ],
)
@pytest.mark.asyncio
async def test_database_refuses_a_ratio_on_the_wrong_kind_of_entry(
    session: AsyncSession, market_asset_api: Asset, kind, old_units, new_units
):
    """The service validates, and the table backs it up: a split without a usable
    ratio, or a ratio on a buy or sell, never reaches the ledger."""
    session.add(
        AssetTransaction(
            asset_id=market_asset_api.id, workspace_id=market_asset_api.workspace_id,
            kind=kind, quantity=Decimal("1"), price=Decimal("1"), fee=Decimal("0"),
            date=date(2026, 1, 1), old_units=old_units, new_units=new_units,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_database_accepts_the_two_valid_shapes(session: AsyncSession, market_asset_api: Asset):
    for kind, old_units, new_units in (("buy", None, None), ("split", 1, 100)):
        session.add(
            AssetTransaction(
                asset_id=market_asset_api.id, workspace_id=market_asset_api.workspace_id,
                kind=kind, quantity=Decimal("1"), price=Decimal("1"), fee=Decimal("0"),
                date=date(2026, 1, 1), old_units=old_units, new_units=new_units,
            )
        )
    await session.commit()
