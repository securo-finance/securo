"""The agent runtime's MCP client as a dual-era client (spec 2026-07-28).

It must speak the modern protocol to Securo's own server and to any modern
user-supplied server, and still fall back cleanly for a legacy one — which is
how every extra MCP server configured before this change was reached.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import app.agents.mcp.client as mcp_client_module
from app.agents.mcp.client import MCPClient, _encode_header_value, _is_modern_refusal, _parse_sse


class _Resp:
    def __init__(self, status_code: int = 200, body: Any = None, *, content_type: str = "application/json", text: str | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _FakeHttp:
    queue: list[_Resp] = []
    calls: list[dict] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, headers=None):  # noqa: A002
        type(self).calls.append({"url": url, "json": json, "headers": headers or {}})
        return type(self).queue.pop(0)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    _FakeHttp.queue = []
    _FakeHttp.calls = []
    monkeypatch.setattr(mcp_client_module.httpx, "AsyncClient", _FakeHttp)
    yield


def _ok(result: dict, rid: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


@pytest.mark.asyncio
async def test_first_request_is_modern_with_mirrored_headers_and_meta():
    _FakeHttp.queue = [_Resp(200, _ok({"resultType": "complete", "tools": []}))]
    client = MCPClient(name="securo", url="http://mcp/mcp")

    await client.list_tools(token="t")

    call = _FakeHttp.calls[0]
    meta = call["json"]["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
    assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "securo-agent-runtime"
    assert call["headers"]["MCP-Protocol-Version"] == "2026-07-28"
    assert call["headers"]["Mcp-Method"] == "tools/list"
    assert "Mcp-Name" not in call["headers"]
    assert "text/event-stream" in call["headers"]["Accept"]
    assert client._era == "modern"


@pytest.mark.asyncio
async def test_tools_call_sends_mcp_name():
    _FakeHttp.queue = [_Resp(200, _ok({"resultType": "complete", "content": [], "structuredContent": {"x": 1}, "isError": False}))]
    client = MCPClient(name="securo", url="http://mcp/mcp")

    out = await client.call_tool(name="list_accounts", arguments={}, token="t")

    assert _FakeHttp.calls[0]["headers"]["Mcp-Name"] == "list_accounts"
    assert _FakeHttp.calls[0]["json"]["params"]["name"] == "list_accounts"
    assert out["ok"] is True and out["data"] == {"x": 1}


@pytest.mark.asyncio
async def test_legacy_server_triggers_one_fallback_and_is_remembered():
    """A 4xx without a modern JSON-RPC error body means a legacy server: resend
    in the plain shape, and never probe that server again."""
    _FakeHttp.queue = [
        _Resp(400, {"detail": "bad request"}),          # legacy server rejects _meta shape
        _Resp(200, _ok({"tools": [{"name": "a"}]})),    # plain retry succeeds
        _Resp(200, _ok({"tools": [{"name": "a"}]})),    # next call goes straight to legacy
    ]
    client = MCPClient(name="extra", url="http://legacy/mcp")

    first = await client.list_tools(token="t")
    second = await client.list_tools(token="t")

    assert [t.name for t in first] == ["a"] and [t.name for t in second] == ["a"]
    assert client._era == "legacy"
    assert len(_FakeHttp.calls) == 3
    assert "_meta" not in _FakeHttp.calls[1]["json"]["params"]
    assert "MCP-Protocol-Version" not in _FakeHttp.calls[1]["headers"]
    assert "_meta" not in _FakeHttp.calls[2]["json"]["params"], "the era is cached, no second probe"


@pytest.mark.asyncio
async def test_modern_refusal_raises_instead_of_falling_back():
    """A recognised modern error means the server is modern — correct the
    request, do not silently downgrade to a protocol it may not serve."""
    _FakeHttp.queue = [_Resp(400, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32022, "message": "Unsupported", "data": {"supported": ["2027-01-01"], "requested": "2026-07-28"}}})]
    client = MCPClient(name="future", url="http://future/mcp")

    with pytest.raises(RuntimeError, match="-32022"):
        await client.list_tools(token="t")
    assert len(_FakeHttp.calls) == 1
    assert client._era == "modern"


@pytest.mark.asyncio
async def test_auth_failure_is_not_mistaken_for_a_legacy_server():
    _FakeHttp.queue = [_Resp(401, {"jsonrpc": "2.0", "error": {"code": -32001, "message": "invalid token"}})]
    client = MCPClient(name="securo", url="http://mcp/mcp")

    with pytest.raises(RuntimeError, match="401"):
        await client.list_tools(token="bad")
    assert client._era is None
    assert len(_FakeHttp.calls) == 1


@pytest.mark.asyncio
async def test_sse_response_is_parsed_for_the_matching_id():
    stream = (
        'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}\n\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"resultType":"complete","tools":[{"name":"s"}]}}\n\n'
    )
    _FakeHttp.queue = [_Resp(200, None, content_type="text/event-stream", text=stream)]
    client = MCPClient(name="streamy", url="http://s/mcp")

    tools = await client.list_tools(token="t")

    assert [t.name for t in tools] == ["s"]


@pytest.mark.asyncio
async def test_input_required_result_is_refused_clearly():
    _FakeHttp.queue = [_Resp(200, _ok({"resultType": "input_required", "inputRequests": {}}))]
    client = MCPClient(name="mrtr", url="http://m/mcp")

    with pytest.raises(RuntimeError, match="client input"):
        await client.call_tool(name="x", arguments={}, token="t")


@pytest.mark.asyncio
async def test_proposal_flag_is_read_from_vendor_meta():
    _FakeHttp.queue = [_Resp(200, _ok({"resultType": "complete", "tools": [
        {"name": "propose_x", "inputSchema": {"type": "object"}, "_meta": {"com.usesecuro/tool": {"is_proposal": True}}},
        {"name": "legacy_y", "_securo": {"is_proposal": True}},
    ]}))]
    client = MCPClient(name="securo", url="http://mcp/mcp")

    tools = await client.list_tools(token="t")

    assert [t.is_proposal for t in tools] == [True, True]


def test_modern_refusal_detection_matches_the_spec():
    assert _is_modern_refusal(400, {"error": {"code": -32020}})
    assert _is_modern_refusal(400, {"error": {"code": -32022}})
    assert _is_modern_refusal(404, {"error": {"code": -32601}})
    assert _is_modern_refusal(400, {"error": {"code": -32602}})
    assert not _is_modern_refusal(404, {"detail": "Not Found"})
    assert not _is_modern_refusal(405, None)
    assert not _is_modern_refusal(400, {"error": {"code": -32600}})


def test_header_value_encoding():
    assert _encode_header_value("list_accounts") == "list_accounts"
    encoded = _encode_header_value("Hello, 世界")
    assert encoded.startswith("=?base64?") and encoded.endswith("?=")
    assert _encode_header_value(" padded ").startswith("=?base64?")
    assert _encode_header_value("=?base64?literal?=").startswith("=?base64?") and _encode_header_value("=?base64?literal?=") != "=?base64?literal?="


def test_sse_parser_ignores_unrelated_events():
    text = 'data: {"jsonrpc":"2.0","id":2,"result":{}}\n\ndata: not json\n\n'
    assert _parse_sse(text, 1) is None
