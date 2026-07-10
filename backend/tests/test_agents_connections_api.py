"""HTTP coverage for /api/agents/connections — LLM connection CRUD plus
the /test probe endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_list_connections_empty(client, auth_headers, test_user):
    r = await client.get("/api/agents/connections", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_connection_persists_and_hides_api_key(client, auth_headers, test_user):
    payload = {
        "name": "My OpenAI",
        "kind": "openai",
        "api_key": "sk-secret-123",
        "default_model": "gpt-4o-mini",
    }
    r = await client.post("/api/agents/connections", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    # has_api_key reflects presence; the literal key never round-trips.
    assert body["has_api_key"] is True
    assert "api_key" not in body
    assert body["default_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_create_connection_400_for_unknown_kind(client, auth_headers, test_user):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "bogus"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "unknown kind" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_connection_400_for_openai_compat_without_base_url(
    client, auth_headers, test_user
):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "openai_compatible"},
        headers=auth_headers,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_update_delete_connection(client, auth_headers, test_user):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "A", "kind": "openai"},
        headers=auth_headers,
    )
    cid = r.json()["id"]

    # GET
    r = await client.get(f"/api/agents/connections/{cid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "A"

    # PATCH
    r = await client.patch(
        f"/api/agents/connections/{cid}",
        json={"name": "Renamed", "is_default": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["is_default"] is True

    # DELETE
    r = await client.delete(f"/api/agents/connections/{cid}", headers=auth_headers)
    assert r.status_code == 204

    # 404 after delete
    r = await client.get(f"/api/agents/connections/{cid}", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_connection_404(client, auth_headers, test_user):
    r = await client.get(f"/api/agents/connections/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_connection_404(client, auth_headers, test_user):
    r = await client.patch(
        f"/api/agents/connections/{uuid.uuid4()}",
        json={"name": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_connection_404(client, auth_headers, test_user):
    r = await client.delete(f"/api/agents/connections/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_test_connection_uses_service_probe(client, auth_headers, test_user):
    """The /test endpoint should delegate to connection_service.test_connection
    and return its structured result."""
    r = await client.post(
        "/api/agents/connections",
        json={"name": "A", "kind": "ollama"},
        headers=auth_headers,
    )
    cid = r.json()["id"]

    async def fake_probe(conn):
        return {"ok": True, "detail": "fake reachable", "models": ["llama3"]}

    with patch(
        "app.agents.api.connections.connection_service.test_connection",
        side_effect=fake_probe,
    ):
        r = await client.post(f"/api/agents/connections/{cid}/test", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["detail"] == "fake reachable"
    assert body["models"] == ["llama3"]


@pytest.mark.asyncio
async def test_test_connection_404_for_unknown(client, auth_headers, test_user):
    r = await client.post(f"/api/agents/connections/{uuid.uuid4()}/test", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_connections_rejected(client):
    r = await client.get("/api/agents/connections")
    assert r.status_code == 401
