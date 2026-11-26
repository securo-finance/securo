"""Hand-recorded trades never replace the position a bank connection reports."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.import_log import ImportLog
from app.services import asset_import_service

pytestmark = pytest.mark.asyncio

REFUSAL = (
    "This holding is synced from a bank connection, "
    "so its trades can't be added or edited by hand."
)
BUY = {"kind": "buy", "quantity": 1, "price": 20, "date": "2026-01-01"}


@pytest_asyncio.fixture(params=["market_price", "manual"])
async def synced(request, session, test_user, test_workspace, test_connection) -> Asset:
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Petrobras", type="stock", currency="BRL", units=Decimal("100"),
        valuation_method=request.param, ticker="PETR4.SA",
        last_price=Decimal("30"), connection_id=test_connection.id,
        source="test", external_id="holding-1",
    )
    session.add(asset)
    await session.commit()
    return asset


async def _row(session, asset, kind="buy", quantity="1", **fields) -> AssetTransaction:
    row = AssetTransaction(
        asset_id=asset.id, workspace_id=asset.workspace_id, kind=kind,
        quantity=Decimal(quantity), price=Decimal("20"), fee=Decimal("0"),
        **{"date": date(2026, 1, 1), "source": "manual", **fields},
    )
    session.add(row)
    await session.commit()
    return row


@pytest.mark.parametrize("kind", ["buy", "sell"])
async def test_adding_a_trade_is_refused(client, auth_headers, session, synced, kind):
    resp = await client.post(
        f"/api/assets/{synced.id}/transactions", headers=auth_headers, json={**BUY, "kind": kind}
    )
    assert (resp.status_code, resp.json()["detail"]) == (422, REFUSAL)
    await session.refresh(synced)
    assert synced.units == 100
    assert (await session.execute(select(AssetTransaction))).first() is None


async def test_buying_by_ticker_into_it_is_refused(client, auth_headers, session, synced):
    synced.valuation_method = "market_price"  # the only kind a ticker matches
    await session.commit()
    resp = await client.post(
        "/api/assets/buy", headers=auth_headers,
        json={"ticker": "PETR4.SA", "quantity": 1, "price": 20, "date": "2026-01-01"},
    )
    assert (resp.status_code, resp.json()["detail"]) == (422, REFUSAL)
    await session.refresh(synced)
    assert synced.units == 100


async def test_only_the_note_of_an_existing_trade_can_be_edited(
    client, auth_headers, session, synced
):
    row = await _row(session, synced)
    url = f"/api/assets/transactions/{row.id}"
    refused = await client.patch(url, headers=auth_headers, json={"quantity": 5})
    assert (refused.status_code, refused.json()["detail"]) == (422, REFUSAL)
    noted = await client.patch(url, headers=auth_headers, json={"notes": "old entry"})
    assert noted.status_code == 200, noted.text
    assert noted.json()["units"] == 100


async def test_deleting_an_existing_trade_keeps_the_units(client, auth_headers, session, synced):
    row = await _row(session, synced)
    resp = await client.delete(f"/api/assets/transactions/{row.id}", headers=auth_headers)
    assert resp.status_code in (200, 204), resp.text
    await session.refresh(synced)
    assert synced.units == 100
    assert synced.sell_date is None


async def test_deleting_a_buy_does_not_mark_the_holding_sold(
    client, auth_headers, session, synced
):
    """The sell left behind replays to zero units, but the bank still holds 100."""
    row = await _row(session, synced)
    await _row(session, synced, kind="sell", date=date(2026, 2, 1))
    resp = await client.delete(f"/api/assets/transactions/{row.id}", headers=auth_headers)
    # Accepted, or refused because the sell depends on it: never a closed holding.
    assert resp.status_code in (200, 422), resp.text
    await session.refresh(synced)
    assert synced.units == 100
    assert synced.sell_date is None


async def test_deleting_the_sell_that_closed_it_reopens_it(
    client, auth_headers, session, synced
):
    await _row(session, synced, quantity="10")
    sell = await _row(session, synced, kind="sell", quantity="10", date=date(2026, 2, 1))
    synced.units, synced.sell_date = Decimal("0"), date(2026, 2, 1)
    await session.commit()
    resp = await client.delete(f"/api/assets/transactions/{sell.id}", headers=auth_headers)
    assert resp.status_code in (200, 204), resp.text
    await session.refresh(synced)
    assert synced.units == 10
    assert synced.sell_date is None


async def test_undoing_an_import_keeps_the_holding(
    session, test_user, test_workspace, synced
):
    log = ImportLog(
        user_id=test_user.id, workspace_id=test_workspace.id, entity="asset_orders",
        filename="orders.csv", format="csv", transaction_count=1,
    )
    session.add(log)
    await session.flush()
    await _row(session, synced, source="import", import_id=log.id)
    assert await asset_import_service.undo_import(session, test_workspace.id, log) == 1
    kept = await session.get(Asset, synced.id)
    assert kept is not None
    assert kept.units == 100
