import uuid

import pytest
from httpx import AsyncClient


async def _create_wallet(
    client: AsyncClient, auth_headers: dict, name: str, position: int
) -> dict:
    response = await client.post(
        "/api/asset-groups",
        headers=auth_headers,
        json={"name": name, "position": position},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_reorder_wallets(client: AsyncClient, auth_headers: dict):
    first = await _create_wallet(client, auth_headers, "First", 1)
    middle = await _create_wallet(client, auth_headers, "Middle", 2)
    last = await _create_wallet(client, auth_headers, "Last", 3)

    response = await client.put(
        "/api/asset-groups/order",
        headers=auth_headers,
        json={"group_ids": [last["id"], first["id"]]},
    )
    assert response.status_code == 204, response.text

    listed = (await client.get("/api/asset-groups", headers=auth_headers)).json()
    assert [wallet["id"] for wallet in listed] == [last["id"], middle["id"], first["id"]]
    assert [wallet["position"] for wallet in listed] == [0, 1, 2]


@pytest.mark.asyncio
async def test_reorder_wallets_rejects_invalid_ids(
    client: AsyncClient, auth_headers: dict
):
    wallet = await _create_wallet(client, auth_headers, "Wallet", 1)

    duplicate = await client.put(
        "/api/asset-groups/order",
        headers=auth_headers,
        json={"group_ids": [wallet["id"], wallet["id"]]},
    )
    assert duplicate.status_code == 422

    missing = await client.put(
        "/api/asset-groups/order",
        headers=auth_headers,
        json={"group_ids": [str(uuid.uuid4())]},
    )
    assert missing.status_code == 404

    too_many = await client.put(
        "/api/asset-groups/order",
        headers=auth_headers,
        json={"group_ids": [str(uuid.uuid4()) for _ in range(1001)]},
    )
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_reorder_wallets_needs_write_access(
    client: AsyncClient, viewer_auth_headers: dict, test_workspace
):
    response = await client.put(
        "/api/asset-groups/order",
        headers=viewer_auth_headers,
        json={"group_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403, response.text
