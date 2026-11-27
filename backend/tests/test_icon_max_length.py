"""An icon longer than its String(50) column is refused with 422, never a database error."""

import pytest
from httpx import AsyncClient

TOO_LONG = "x" * 51
LONGEST = "x" * 50
SQL_MARKERS = ("sqlalchemy", "asyncpg", "varying", "truncat", "INSERT", "UPDATE")


def _assert_icon_rejected(response) -> None:
    assert response.status_code == 422, response.text
    body = response.text
    for marker in SQL_MARKERS:
        assert marker not in body
    assert any(err["loc"][-1] == "icon" for err in response.json()["detail"])


@pytest.mark.asyncio
async def test_category_icon_over_50_chars_is_422(client: AsyncClient, auth_headers):
    created = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Long icon", "icon": TOO_LONG, "color": "#000000"},
    )
    _assert_icon_rejected(created)

    ok = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Fits", "icon": LONGEST, "color": "#000000"},
    )
    assert ok.status_code == 201
    updated = await client.patch(
        f"/api/categories/{ok.json()['id']}",
        headers=auth_headers,
        json={"icon": TOO_LONG},
    )
    _assert_icon_rejected(updated)


@pytest.mark.asyncio
async def test_category_group_icon_over_50_chars_is_422(client: AsyncClient, auth_headers):
    created = await client.post(
        "/api/category-groups",
        headers=auth_headers,
        json={"name": "Long icon", "icon": TOO_LONG, "color": "#000000"},
    )
    _assert_icon_rejected(created)

    ok = await client.post(
        "/api/category-groups",
        headers=auth_headers,
        json={"name": "Fits", "icon": LONGEST, "color": "#000000"},
    )
    assert ok.status_code == 201
    updated = await client.patch(
        f"/api/category-groups/{ok.json()['id']}",
        headers=auth_headers,
        json={"icon": TOO_LONG},
    )
    _assert_icon_rejected(updated)


@pytest.mark.asyncio
async def test_goal_icon_over_50_chars_is_422(client: AsyncClient, auth_headers):
    goal = {"name": "Trip", "target_amount": "1000.00", "currency": "BRL"}
    created = await client.post(
        "/api/goals", headers=auth_headers, json={**goal, "icon": TOO_LONG}
    )
    _assert_icon_rejected(created)

    ok = await client.post(
        "/api/goals", headers=auth_headers, json={**goal, "icon": LONGEST}
    )
    assert ok.status_code == 201
    updated = await client.patch(
        f"/api/goals/{ok.json()['id']}",
        headers=auth_headers,
        json={"icon": TOO_LONG},
    )
    _assert_icon_rejected(updated)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/asset-groups", {"name": "Wallet"}),
        ("/api/collections", {"name": "Household"}),
        ("/api/groups", {"name": "Trip", "kind": "social", "default_currency": "USD"}),
    ],
)
async def test_icon_over_50_chars_is_422(client: AsyncClient, auth_headers, path, payload):
    created = await client.post(path, headers=auth_headers, json={**payload, "icon": TOO_LONG})
    _assert_icon_rejected(created)

    ok = await client.post(path, headers=auth_headers, json={**payload, "icon": LONGEST})
    assert ok.status_code == 201, ok.text
    updated = await client.patch(
        f"{path}/{ok.json()['id']}", headers=auth_headers, json={"icon": TOO_LONG}
    )
    _assert_icon_rejected(updated)
