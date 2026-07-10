"""Cover the MCP server's HTTP surface (mcp_server/main.py).

Strategy: drive the FastAPI app directly with httpx.ASGITransport so we
exercise auth + JSON-RPC routing without needing the agents backend
running. Tool calls go against the SQLite test DB and are limited to
read-only paths (avoid pgvector-dependent tools).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.mcp.auth import mint_token


def _client():
    # Import here so the import counts toward coverage and the test DB
    # is already configured by conftest before mcp_server.main loads.
    from mcp_server.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://mcp.test")


def _auth_headers(user_id) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id)}"}


@pytest.mark.asyncio
async def test_health_endpoint_lists_registered_tool_count(test_user):
    async with _client() as cli:
        r = await cli.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tools"] > 0


@pytest.mark.asyncio
async def test_mcp_rejects_unauthenticated():
    async with _client() as cli:
        r = await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_mcp_rejects_bad_token():
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer not.a.real.token"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_mcp_rejects_malformed_body(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", content="not json", headers=_auth_headers(test_user.id))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_mcp_rejects_non_object_body(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", json=[1, 2, 3], headers=_auth_headers(test_user.id))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_mcp_rejects_wrong_jsonrpc_version(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "1.0", "id": 7, "method": "tools/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_mcp_rejects_non_string_method(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": 42},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_mcp_initialize_returns_protocol_handshake(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "securo-builtin"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_mcp_tools_list(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    # A representative sampling — these all live in mcp_server/tools/.
    assert {"list_accounts", "list_categories", "list_payees", "aggregate", "list_groups"} <= names


@pytest.mark.asyncio
async def test_mcp_tools_call_missing_name(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_mcp_tools_call_unknown_tool(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            },
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_unknown_method(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_tools_call_runs_real_tool(test_user, monkeypatch):
    """End-to-end happy path: auth → tools/call → real tool → structured
    JSON response. Uses list_categories because it has no pgvector
    dependency and is safe on SQLite.

    Routes the mcp_server's `async_session_maker` to the conftest test
    DB; otherwise CI's mcp_server points at the real Postgres which has
    no schema in the test environment and the tool raises."""
    from tests.conftest import TestSessionLocal
    import mcp_server.main as mcp_main

    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)

    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "list_categories", "arguments": {}},
            },
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    body = r.json()
    result = body["result"]
    assert result["isError"] is False
    # `structuredContent` mirrors the tool's dict return value.
    assert "items" in result["structuredContent"]
    # `content[0].text` is JSON-encoded for clients that prefer text.
    parsed = json.loads(result["content"][0]["text"])
    assert "items" in parsed
