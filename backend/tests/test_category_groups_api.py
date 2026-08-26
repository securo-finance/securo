import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.category_service import create_default_categories


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
        f"/api/category-groups/{group_id}", headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_group_not_found(client: AsyncClient, auth_headers):
    response = await client.delete(
        f"/api/category-groups/{uuid.uuid4()}", headers=auth_headers,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_hide_system_group_filters_default_list(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user
):
    await create_default_categories(session, test_user.id, "pt-BR")
    groups_response = await client.get("/api/category-groups", headers=auth_headers)
    group_id = groups_response.json()[0]["id"]

    update_response = await client.patch(
        f"/api/category-groups/{group_id}",
        json={"is_hidden": True},
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["is_hidden"] is True

    response = await client.get("/api/category-groups", headers=auth_headers)
    assert response.status_code == 200
    assert group_id not in {group["id"] for group in response.json()}

    categories_response = await client.get("/api/categories", headers=auth_headers)
    assert categories_response.status_code == 200
    assert group_id not in {category["group_id"] for category in categories_response.json()}

    include_hidden_response = await client.get(
        "/api/category-groups?include_hidden=true",
        headers=auth_headers,
    )
    assert include_hidden_response.status_code == 200
    hidden_group = next(
        group for group in include_hidden_response.json() if group["id"] == group_id
    )
    assert hidden_group["is_hidden"] is True


@pytest.mark.asyncio
async def test_cannot_hide_user_group(client: AsyncClient, auth_headers):
    create_response = await client.post(
        "/api/category-groups",
        json={"name": "Custom"},
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    group_id = create_response.json()["id"]

    update_response = await client.patch(
        f"/api/category-groups/{group_id}",
        json={"is_hidden": True},
        headers=auth_headers,
    )

    assert update_response.status_code == 400
    assert update_response.json()["detail"] == "Only system category groups can be hidden"

    unhide_response = await client.patch(
        f"/api/category-groups/{group_id}",
        json={"is_hidden": False},
        headers=auth_headers,
    )
    assert unhide_response.status_code == 200
    assert unhide_response.json()["is_hidden"] is False

    list_response = await client.get(
        "/api/category-groups?include_hidden=true",
        headers=auth_headers,
    )
    group = next(item for item in list_response.json() if item["id"] == group_id)
    assert group["is_hidden"] is False


@pytest.mark.asyncio
async def test_group_list_filters_hidden_child_categories(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user
):
    await create_default_categories(session, test_user.id, "pt-BR")
    categories_response = await client.get("/api/categories", headers=auth_headers)
    category_id = categories_response.json()[0]["id"]

    hide_response = await client.patch(
        f"/api/categories/{category_id}",
        json={"is_hidden": True},
        headers=auth_headers,
    )
    assert hide_response.status_code == 200

    response = await client.get("/api/category-groups", headers=auth_headers)
    assert response.status_code == 200
    nested_ids = {
        category["id"]
        for group in response.json()
        for category in group["categories"]
    }
    assert category_id not in nested_ids

    include_hidden_response = await client.get(
        "/api/category-groups?include_hidden=true",
        headers=auth_headers,
    )
    assert include_hidden_response.status_code == 200
    nested_hidden_ids = {
        category["id"]
        for group in include_hidden_response.json()
        for category in group["categories"]
    }
    assert category_id in nested_hidden_ids
