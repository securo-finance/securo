import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_groups(client: AsyncClient, auth_headers):
    response = await client.get("/api/category-groups", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_group(client: AsyncClient, auth_headers):
    response = await client.post(
        "/api/category-groups",
        json={"name": "Housing", "icon": "home", "color": "#3B82F6"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Housing"


@pytest.mark.asyncio
async def test_update_group(client: AsyncClient, auth_headers):
    create_resp = await client.post(
        "/api/category-groups",
        json={"name": "Temp", "icon": "x", "color": "#000"},
        headers=auth_headers,
    )
    group_id = create_resp.json()["id"]
    response = await client.patch(
        f"/api/category-groups/{group_id}",
        json={"name": "Updated"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated"


@pytest.mark.asyncio
async def test_update_group_not_found(client: AsyncClient, auth_headers):
    response = await client.patch(
        f"/api/category-groups/{uuid.uuid4()}",
        json={"name": "X"},
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_group(client: AsyncClient, auth_headers):
    create_resp = await client.post(
        "/api/category-groups",
        json={"name": "Del Group", "icon": "x", "color": "#000"},
        headers=auth_headers,
    )
    group_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/category-groups/{group_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_group_not_found(client: AsyncClient, auth_headers):
    response = await client.delete(
        f"/api/category-groups/{uuid.uuid4()}",
        headers=auth_headers,
    )
    assert response.status_code == 400
