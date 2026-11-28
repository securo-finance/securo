"""Putting holdings in the order someone chose (PUT /api/assets/order)."""

import uuid
from typing import Optional

import pytest
from httpx import AsyncClient

from app.models.asset import Asset
from app.services.ordering import place_in_order


async def _wallet(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post("/api/asset-groups", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _holding(client: AsyncClient, headers: dict, name: str, group_id: Optional[str]) -> str:
    response = await client.post(
        "/api/assets",
        headers=headers,
        json={"name": name, "type": "investment", "currency": "USD", "current_value": 100, "group_id": group_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _names(client: AsyncClient, headers: dict, group_id: Optional[str], include_archived: bool = False):
    response = await client.get(
        "/api/assets", headers=headers, params={"include_archived": include_archived}
    )
    return [a["name"] for a in response.json() if a["group_id"] == group_id]


async def _reorder(client: AsyncClient, headers: dict, ids: list[str]):
    return await client.put("/api/assets/order", headers=headers, json={"asset_ids": ids})


@pytest.mark.asyncio
async def test_holdings_list_in_the_saved_order(client: AsyncClient, auth_headers: dict):
    wallet = await _wallet(client, auth_headers, "Brokerage")
    alpha = await _holding(client, auth_headers, "Alpha", wallet)
    beta = await _holding(client, auth_headers, "Beta", wallet)
    gamma = await _holding(client, auth_headers, "Gamma", wallet)

    response = await _reorder(client, auth_headers, [gamma, alpha, beta])

    assert response.status_code == 204, response.text
    assert await _names(client, auth_headers, wallet) == ["Gamma", "Alpha", "Beta"]


@pytest.mark.asyncio
async def test_ungrouped_holdings_can_be_ordered(client: AsyncClient, auth_headers: dict):
    first = await _holding(client, auth_headers, "First", None)
    second = await _holding(client, auth_headers, "Second", None)

    assert (await _reorder(client, auth_headers, [second, first])).status_code == 204

    assert await _names(client, auth_headers, None) == ["Second", "First"]


@pytest.mark.asyncio
async def test_a_hidden_holding_keeps_its_place(client: AsyncClient, auth_headers: dict):
    wallet = await _wallet(client, auth_headers, "Brokerage")
    alpha = await _holding(client, auth_headers, "Alpha", wallet)
    beta = await _holding(client, auth_headers, "Beta", wallet)
    gamma = await _holding(client, auth_headers, "Gamma", wallet)
    archived = await client.patch(f"/api/assets/{beta}", headers=auth_headers, json={"is_archived": True})
    assert archived.status_code == 200

    # The page sends only what it shows: Beta is archived and not on screen.
    assert (await _reorder(client, auth_headers, [gamma, alpha])).status_code == 204

    assert await _names(client, auth_headers, wallet, include_archived=True) == ["Gamma", "Beta", "Alpha"]


@pytest.mark.asyncio
async def test_new_and_moved_holdings_go_after_the_placed_ones(client: AsyncClient, auth_headers: dict):
    wallet = await _wallet(client, auth_headers, "Brokerage")
    other = await _wallet(client, auth_headers, "Pension")
    beta = await _holding(client, auth_headers, "Beta", wallet)
    gamma = await _holding(client, auth_headers, "Gamma", wallet)
    assert (await _reorder(client, auth_headers, [gamma, beta])).status_code == 204

    await _holding(client, auth_headers, "Aardvark", wallet)
    moving = await _holding(client, auth_headers, "Able", other)
    # Placed first in its old wallet: that place must not come along.
    assert (await _reorder(client, auth_headers, [moving])).status_code == 204
    moved = await client.patch(f"/api/assets/{moving}", headers=auth_headers, json={"group_id": wallet})
    assert moved.status_code == 200

    # Named first alphabetically, yet both land after the order that was set.
    assert await _names(client, auth_headers, wallet) == ["Gamma", "Beta", "Aardvark", "Able"]


@pytest.mark.asyncio
async def test_saving_a_holding_in_its_own_wallet_keeps_its_place(client: AsyncClient, auth_headers: dict):
    wallet = await _wallet(client, auth_headers, "Brokerage")
    alpha = await _holding(client, auth_headers, "Alpha", wallet)
    beta = await _holding(client, auth_headers, "Beta", wallet)
    assert (await _reorder(client, auth_headers, [beta, alpha])).status_code == 204

    # The edit form always sends the wallet, changed or not.
    edited = await client.patch(
        f"/api/assets/{beta}", headers=auth_headers, json={"name": "Beta fund", "group_id": wallet}
    )

    assert edited.status_code == 200
    assert await _names(client, auth_headers, wallet) == ["Beta fund", "Alpha"]


@pytest.mark.asyncio
async def test_rejects_holdings_from_two_wallets(client: AsyncClient, auth_headers: dict):
    first = await _holding(client, auth_headers, "First", await _wallet(client, auth_headers, "A"))
    second = await _holding(client, auth_headers, "Second", await _wallet(client, auth_headers, "B"))

    response = await _reorder(client, auth_headers, [first, second])

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rejects_unknown_and_repeated_ids(client: AsyncClient, auth_headers: dict):
    holding = await _holding(client, auth_headers, "Only", None)

    assert (await _reorder(client, auth_headers, [holding, str(uuid.uuid4())])).status_code == 404
    assert (await _reorder(client, auth_headers, [holding, holding])).status_code == 422
    assert (await _reorder(client, auth_headers, [])).status_code == 422


@pytest.mark.asyncio
async def test_a_viewer_cannot_reorder(client: AsyncClient, viewer_auth_headers: dict):
    response = await _reorder(client, viewer_auth_headers, [str(uuid.uuid4())])

    assert response.status_code == 403


def test_place_in_order_trades_only_the_named_slots():
    rows = [Asset(id=uuid.uuid4(), name=name) for name in ("a", "b", "c", "d")]
    a, b, c, d = rows

    assert place_in_order(rows, [d.id, a.id]) == [d, b, c, a]
    assert place_in_order(rows, [uuid.uuid4()]) is None
