"""Cover app/agents/mcp/client.py — the JSON-RPC client used by the
agents runtime to talk to MCP servers.

Strategy: replace httpx.AsyncClient with a thin fake driven by a queue
of canned responses so each test can shape the wire protocol without
the test process opening real sockets.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

import app.agents.mcp.client as mcp_client_module
from app.agents.mcp.client import (
    MCPClient,
    MCPRegistry,
    ToolHandle,
    _join_text,
    _parse_servers,
)


# --------------------------------------------------------------------- helpers

class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: Any = None):
        self.status_code = status_code
        self._body = json_body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            # Discover swallows Exception, so the type doesn't matter here.
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._body


class _FakeAsyncClient:
    """Records calls, returns queued responses. Implements just enough
    of httpx.AsyncClient for MCPClient._post to be happy."""

    def __init__(self, *_, **__):  # accept (timeout=...) kw
        pass

    queue: list[_FakeResponse] = []
    calls: list[tuple[str, dict | None, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, headers=None):  # noqa: A002
        type(self).calls.append((url, json, headers))
        if not type(self).queue:
            return _FakeResponse(status_code=200, json_body={"jsonrpc": "2.0", "id": 1, "result": {}})
        return type(self).queue.pop(0)


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    _FakeAsyncClient.queue = []
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(mcp_client_module.httpx, "AsyncClient", _FakeAsyncClient)
    yield


# --------------------------------------------------------------------- _join_text

def test_join_text_concatenates_text_parts():
    out = _join_text([{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}])
    assert out == "hi\nthere"


def test_join_text_ignores_non_text_and_non_list():
    assert _join_text(None) == ""
    assert _join_text("not a list") == ""
    assert _join_text([{"type": "image", "url": "x"}]) == ""
    # Mixed: only text parts survive.
    assert _join_text([{"type": "text", "text": "a"}, {"type": "image"}]) == "a"


# --------------------------------------------------------------------- _parse_servers

def test_parse_servers_includes_builtin_only_by_default(monkeypatch):
    from app.agents.config import get_agent_settings

    s = get_agent_settings()
    monkeypatch.setattr(s, "extra_mcp_servers", "")
    out = _parse_servers()
    assert [sp.name for sp in out] == ["securo"]


def test_parse_servers_handles_extra_with_and_without_alias(monkeypatch):
    from app.agents.config import get_agent_settings

    s = get_agent_settings()
    monkeypatch.setattr(s, "extra_mcp_servers", "http://a:9000/mcp|alpha, http://b:9001/mcp, , http://c:9002/mcp|gamma")
    out = _parse_servers()
    names = [sp.name for sp in out]
    urls = {sp.name: sp.url for sp in out}
    # securo + 3 extras (empty entry is skipped)
    assert names == ["securo", "alpha", "http://b:9001/mcp", "gamma"]
    assert urls["alpha"] == "http://a:9000/mcp"
    assert urls["gamma"] == "http://c:9002/mcp"


# --------------------------------------------------------------------- MCPClient.list_tools

@pytest.mark.asyncio
async def test_list_tools_parses_securo_extras_and_defaults_schema():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": "list_accounts",
                    "description": "List accounts",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "create_payee",
                    "description": "Create a payee",
                    # no inputSchema — should default
                    "_securo": {"is_proposal": True},
                },
            ]
        },
    }))

    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    handles = await client.list_tools(token="fake")

    assert len(handles) == 2
    assert {h.name for h in handles} == {"list_accounts", "create_payee"}
    by_name = {h.name: h for h in handles}
    assert by_name["create_payee"].is_proposal is True
    assert by_name["create_payee"].parameters == {"type": "object", "properties": {}}
    # Bearer header is propagated.
    _, _, headers = _FakeAsyncClient.calls[0]

    assert headers is not None
    assert headers["Authorization"] == "Bearer fake"


@pytest.mark.asyncio
async def test_list_tools_handles_missing_tools_array():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0", "id": 1, "result": None,
    }))
    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    assert await client.list_tools(token="fake") == []


# --------------------------------------------------------------------- MCPClient._post error paths

@pytest.mark.asyncio
async def test_post_raises_runtime_error_when_rpc_error_present():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "method not found"},
    }))
    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    with pytest.raises(RuntimeError, match="method not found"):
        await client.list_tools(token="fake")


# --------------------------------------------------------------------- MCPClient.call_tool

@pytest.mark.asyncio
async def test_call_tool_unpacks_structured_content_and_text():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "isError": False,
            "structuredContent": {"items": [1, 2, 3]},
            "content": [{"type": "text", "text": "ok"}],
        },
    }))
    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    out = await client.call_tool(name="list_accounts", arguments={}, token="fake")
    assert out == {"ok": True, "data": {"items": [1, 2, 3]}, "text": "ok"}


@pytest.mark.asyncio
async def test_call_tool_marks_error_when_is_error_true():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "isError": True,
            "structuredContent": {"error": "bad input"},
            "content": [{"type": "text", "text": "bad input"}],
        },
    }))
    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    out = await client.call_tool(name="x", arguments={}, token="fake")
    assert out["ok"] is False
    assert out["data"] == {"error": "bad input"}


@pytest.mark.asyncio
async def test_call_tool_falls_back_when_no_structured_content():
    """Servers that don't emit structuredContent still get a sensible
    response shape — `data` is the raw result, ok defaults from isError."""
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": "raw"}], "isError": False},
    }))
    client = MCPClient(name="securo", url="http://mcp.test/mcp")
    out = await client.call_tool(name="x", arguments={}, token="fake")
    assert out["ok"] is True
    assert out["text"] == ""
    # data is the raw dict, no structuredContent unpacking.
    assert out["data"] == {"content": [{"type": "text", "text": "raw"}], "isError": False}


# --------------------------------------------------------------------- MCPRegistry

def test_to_provider_tools_namespaces_and_filters():
    handles = [
        ToolHandle(server="securo", name="list_accounts", description="d1", parameters={"type": "object"}),
        ToolHandle(server="securo", name="create_payee", description="d2", parameters={}),
        ToolHandle(server="extra", name="list_accounts", description="d3", parameters={}),
    ]
    # No filter → all three.
    all_tools = MCPRegistry.to_provider_tools(handles, allowed=None)
    assert {t.name for t in all_tools} == {"securo__list_accounts", "securo__create_payee", "extra__list_accounts"}

    # Filter by (server, name) pair.
    filtered = MCPRegistry.to_provider_tools(handles, allowed={("securo", "list_accounts")})
    assert [t.name for t in filtered] == ["securo__list_accounts"]
    # Defaults a missing parameters schema to an empty object schema.
    handle_no_schema = ToolHandle(server="x", name="y", description="d", parameters={})
    out = MCPRegistry.to_provider_tools([handle_no_schema], allowed=None)
    assert out[0].parameters == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_registry_discover_aggregates_across_servers(monkeypatch):
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "extra_mcp_servers", "http://extra:9000/mcp|extra")

    # Two list_tools responses — one per registered server.
    _FakeAsyncClient.queue.extend([
        _FakeResponse(json_body={"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "a", "description": "", "inputSchema": {}}]}}),
        _FakeResponse(json_body={"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "b", "description": "", "inputSchema": {}}]}}),
    ])

    reg = MCPRegistry()
    handles = await reg.discover(user_id=uuid.uuid4())
    names = {(h.server, h.name) for h in handles}
    assert ("securo", "a") in names
    assert ("extra", "b") in names


@pytest.mark.asyncio
async def test_registry_discover_swallows_per_server_errors(monkeypatch):
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "extra_mcp_servers", "http://down:9000/mcp|down")

    # First server returns tools, second errors out — discover should
    # surface only the successful one.
    _FakeAsyncClient.queue.extend([
        _FakeResponse(json_body={"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "a", "description": "", "inputSchema": {}}]}}),
        _FakeResponse(status_code=500, json_body={}),
    ])

    reg = MCPRegistry()
    handles = await reg.discover(user_id=uuid.uuid4())
    assert {h.name for h in handles} == {"a"}


@pytest.mark.asyncio
async def test_registry_discover_warns_when_a_server_is_unreachable(monkeypatch, caplog):
    """An unreachable server leaves the agent with no tools while the chat
    still answers, so the log has to name the server and its URL. Otherwise
    the only symptom is an assistant saying it can't see any data."""
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "extra_mcp_servers", "")
    monkeypatch.setattr(get_agent_settings(), "builtin_mcp_url", "http://mcp-server:8765/mcp")
    _FakeAsyncClient.queue.append(_FakeResponse(status_code=500, json_body={}))

    reg = MCPRegistry()
    with caplog.at_level("WARNING", logger="app.agents.mcp.client"):
        handles = await reg.discover(user_id=uuid.uuid4())

    assert handles == []
    assert "http://mcp-server:8765/mcp" in caplog.text
    assert "securo" in caplog.text


@pytest.mark.asyncio
async def test_registry_call_routes_via_namespaced_name():
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0", "id": 1, "result": {"isError": False, "structuredContent": {"hello": "world"}, "content": []},
    }))
    reg = MCPRegistry()
    out = await reg.call(wire_name="securo__list_accounts", arguments={}, user_id=uuid.uuid4())
    assert out["ok"] is True
    assert out["data"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_registry_call_falls_back_to_bare_name(monkeypatch):
    """If the LLM drops the namespace prefix, registry must scan servers
    and route to whoever exposes that tool."""
    # The registry will call list_tools (to find the tool), then call_tool.
    _FakeAsyncClient.queue.extend([
        _FakeResponse(json_body={"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "list_accounts", "description": "", "inputSchema": {}}]}}),
        _FakeResponse(json_body={"jsonrpc": "2.0", "id": 2, "result": {
            "isError": False, "structuredContent": {"items": []}, "content": []}}),
    ])
    reg = MCPRegistry()
    out = await reg.call(wire_name="list_accounts", arguments={}, user_id=uuid.uuid4())
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_registry_call_raises_on_unknown_tool():
    # list_tools on the only server (securo) returns no tools → unknown.
    _FakeAsyncClient.queue.append(_FakeResponse(json_body={
        "jsonrpc": "2.0", "id": 1, "result": {"tools": []},
    }))
    reg = MCPRegistry()
    with pytest.raises(ValueError, match="unknown tool"):
        await reg.call(wire_name="ghost_tool", arguments={}, user_id=uuid.uuid4())


def test_registry_exposes_server_names(monkeypatch):
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "extra_mcp_servers", "http://b:9001/mcp|beta")
    reg = MCPRegistry()
    assert set(reg.server_names()) == {"securo", "beta"}
