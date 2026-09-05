"""`enabled_modules` reaches the client on every workspace read path.

The frontend hides any module missing from this list, so a response that
omits it is not a cosmetic bug — it blanks the navigation.
"""
import pytest
from httpx import AsyncClient

from app.services.module_service import resolve_modules
from app.models.workspace import Workspace


@pytest.mark.asyncio
async def test_listing_includes_enabled_modules(client: AsyncClient, auth_headers, test_user):
    resp = await client.get("/api/workspaces", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        assert item["enabled_modules"], item


@pytest.mark.asyncio
async def test_current_workspace_includes_enabled_modules(
    client: AsyncClient, auth_headers, test_workspace
):
    resp = await client.get("/api/workspaces/current", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert "invoices" not in resp.json()["enabled_modules"]


@pytest.mark.asyncio
async def test_create_and_update_include_enabled_modules(
    client: AsyncClient, auth_headers, test_user
):
    created = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Consultoria", "kind": "business", "self_membership": True},
    )
    assert created.status_code == 201, created.text
    assert "invoices" in created.json()["enabled_modules"]

    workspace_id = created.json()["id"]
    patched = await client.patch(
        f"/api/workspaces/{workspace_id}",
        headers=auth_headers,
        json={"name": "Consultoria BR"},
    )
    assert patched.status_code == 200, patched.text
    assert "invoices" in patched.json()["enabled_modules"]


@pytest.mark.asyncio
async def test_archive_includes_enabled_modules(client: AsyncClient, auth_headers, test_workspace):
    created = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Temp", "self_membership": True},
    )
    workspace_id = created.json()["id"]
    resp = await client.post(f"/api/workspaces/{workspace_id}/archive", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled_modules"]


@pytest.mark.asyncio
async def test_response_matches_the_resolver(client: AsyncClient, auth_headers, test_workspace):
    """The API must not compute its own answer."""
    resp = await client.get("/api/workspaces/current", headers=auth_headers)
    expected = resolve_modules(Workspace(name="x", kind=test_workspace.kind))
    assert resp.json()["enabled_modules"] == expected
