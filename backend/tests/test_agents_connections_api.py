"""HTTP coverage for /api/agents/connections — LLM connection CRUD plus
the /test probe endpoint."""
from __future__ import annotations

import uuid

import pytest


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
async def test_create_connection_400_for_openai_compat_without_base_url(client, auth_headers, test_user):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "openai_compatible"},
        headers=auth_headers,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://host.docker.internal:11434.",  # trailing period copied from text
        "http://localhost:99999",
        "ftp://example.com",
        "localhost:11434",
        "http://",
        "http://exa mple.com",
    ],
)
async def test_create_connection_400_for_invalid_base_url(client, auth_headers, test_user, base_url):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "ollama", "base_url": base_url},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Enter a valid http or https endpoint URL"


@pytest.mark.asyncio
async def test_create_connection_strips_base_url(client, auth_headers, test_user):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "ollama", "base_url": "  http://host.docker.internal:11434 "},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["base_url"] == "http://host.docker.internal:11434"


@pytest.mark.asyncio
async def test_update_connection_400_for_invalid_base_url_keeps_old_value(client, auth_headers, test_user):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "ollama", "base_url": "http://ollama:11434"},
        headers=auth_headers,
    )
    cid = r.json()["id"]

    r = await client.patch(
        f"/api/agents/connections/{cid}",
        json={"base_url": "http://ollama:11434."},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Enter a valid http or https endpoint URL"

    r = await client.get(f"/api/agents/connections/{cid}", headers=auth_headers)
    assert r.json()["base_url"] == "http://ollama:11434"


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
async def test_update_connection_404_for_unknown_id_with_invalid_base_url(client, auth_headers, test_user):
    r = await client.patch(
        f"/api/agents/connections/{uuid.uuid4()}",
        json={"base_url": "http://ollama:11434."},
        headers=auth_headers,
    )
    assert r.status_code == 404
