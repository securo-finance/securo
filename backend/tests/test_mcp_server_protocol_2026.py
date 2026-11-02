"""The MCP server against the 2026-07-28 specification.

Every assertion here traces to a MUST/SHOULD in
https://modelcontextprotocol.io/specification/2026-07-28/ — transports
(Streamable HTTP), versioning, the base protocol, server/discover, tools and
caching. Legacy behaviour is kept and covered too, because the server is
dual-era: Securo's in-app agent and every pre-2026 client still speak it.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.agents.mcp.auth import mint_token

MODERN = "2026-07-28"


def _client():
    from mcp_server.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.test")


def _meta(version: str = MODERN, **extra) -> dict:
    meta = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "spec-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    meta.update(extra)
    return meta


def _modern_headers(user_id, method: str, *, name: str | None = None, version: str = MODERN) -> dict:
    headers = {
        "Authorization": f"Bearer {mint_token(user_id=user_id)}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _auth(user_id) -> dict:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id)}"}


@pytest.fixture
def fake_tool_run(monkeypatch):
    """tools/call without a database: record the call, return a canned dict."""
    import mcp_server.main as mcp_main

    calls: list[tuple[str, dict | None]] = []

    async def _call(session, ctx, name, arguments):
        calls.append((name, arguments))
        return {"items": [], "total": 0}

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(mcp_main, "call_tool", _call)
    monkeypatch.setattr(mcp_main, "async_session_maker", lambda: _Session())
    return calls


# --------------------------------------------------------------- server/discover


@pytest.mark.asyncio
async def test_discover_is_implemented_and_advertises_versions_and_capabilities(test_user):
    """Servers MUST implement server/discover (server/discover.md)."""
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "d1", "method": "server/discover", "params": {"_meta": _meta()}},
            headers=_modern_headers(test_user.id, "server/discover"),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "d1"
    result = body["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"][0] == MODERN
    assert "2025-11-25" in result["supportedVersions"], "dual-era: legacy versions are genuinely served"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "securo-builtin"
    # Caching hints are required on discover (utilities/caching.md).
    assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
    assert result["cacheScope"] in ("public", "private")


# --------------------------------------------------------------- tools/list


@pytest.mark.asyncio
async def test_modern_tools_list_carries_result_type_caching_and_server_info(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}},
            headers=_modern_headers(test_user.id, "tools/list"),
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] >= 0
    assert result["cacheScope"] == "public", "the tool set is identical for every caller"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "securo-builtin"
    assert len(result["tools"]) > 0


@pytest.mark.asyncio
async def test_every_tool_is_schema_valid_for_2026(test_user):
    """Tool.inputSchema MUST have type "object" at the root, and a modern
    Tool carries no non-standard top-level properties."""
    allowed_keys = {"name", "title", "description", "icons", "inputSchema", "outputSchema", "annotations", "_meta"}
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}},
            headers=_modern_headers(test_user.id, "tools/list"),
        )
    for tool in r.json()["result"]["tools"]:
        assert tool["inputSchema"].get("type") == "object", tool["name"]
        assert set(tool) <= allowed_keys, f"{tool['name']} has {set(tool) - allowed_keys}"
        assert "com.usesecuro/tool" in tool["_meta"], "Securo extras live under a vendor _meta key"


@pytest.mark.asyncio
async def test_tool_list_order_is_deterministic(test_user):
    """Servers SHOULD return tools in a deterministic order."""
    async with _client() as cli:
        names = []
        for i in range(2):
            r = await cli.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {"_meta": _meta()}},
                headers=_modern_headers(test_user.id, "tools/list"),
            )
            names.append([t["name"] for t in r.json()["result"]["tools"]])
    assert names[0] == names[1]


@pytest.mark.asyncio
async def test_annotations_tell_a_gateway_which_tools_write(test_user):
    """Reads are read-only; proposals are not. Only proposals that just add a
    row claim to be additive; everything else that writes keeps the spec's
    cautious default (destructiveHint absent = true)."""
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}},
            headers=_modern_headers(test_user.id, "tools/list"),
        )
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}

    assert tools["list_transactions"]["annotations"]["readOnlyHint"] is True
    assert tools["propose_create_transaction"]["annotations"]["readOnlyHint"] is False
    assert tools["propose_create_transaction"]["annotations"]["destructiveHint"] is False
    # Bulk overwrite and hard delete must not be advertised as harmless.
    assert "destructiveHint" not in tools["propose_categorize"]["annotations"]
    assert "destructiveHint" not in tools["propose_cancel_recurring_transaction"]["annotations"]
    # A new rule can be applied to existing transactions and overwrite their
    # categories, so "create" in the name is not enough.
    assert "destructiveHint" not in tools["propose_create_rule"]["annotations"]
    for name, tool in tools.items():
        is_proposal = tool["_meta"]["com.usesecuro/tool"]["is_proposal"]
        assert tool["annotations"]["readOnlyHint"] is (not is_proposal), name


# --------------------------------------------------------------- tools/call


@pytest.mark.asyncio
async def test_modern_tools_call_returns_result_type_and_structured_content(test_user, fake_tool_run):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "list_categories", "arguments": {}, "_meta": _meta()},
            },
            headers=_modern_headers(test_user.id, "tools/call", name="list_categories"),
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["resultType"] == "complete"
    assert result["isError"] is False
    assert result["structuredContent"] == {"items": [], "total": 0}
    assert json.loads(result["content"][0]["text"]) == {"items": [], "total": 0}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "securo-builtin"
    assert fake_tool_run == [("list_categories", {})]


@pytest.mark.asyncio
async def test_a_failing_tool_is_an_execution_error_not_a_protocol_error(test_user, monkeypatch):
    import mcp_server.main as mcp_main

    async def _boom(session, ctx, name, arguments):
        raise ValueError("date must be in the future")

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(mcp_main, "call_tool", _boom)
    monkeypatch.setattr(mcp_main, "async_session_maker", lambda: _Session())

    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "list_categories", "arguments": {}, "_meta": _meta()},
            },
            headers=_modern_headers(test_user.id, "tools/call", name="list_categories"),
        )
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["resultType"] == "complete"
    assert "date must be in the future" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_a_key_error_inside_a_tool_is_not_reported_as_an_unknown_tool(test_user, monkeypatch):
    """The old server caught KeyError and called it "unknown tool", which hid
    real bugs in tools that raise KeyError internally."""
    import mcp_server.main as mcp_main

    async def _bug(session, ctx, name, arguments):
        raise KeyError("missing_dict_key")

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(mcp_main, "call_tool", _bug)
    monkeypatch.setattr(mcp_main, "async_session_maker", lambda: _Session())

    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "list_categories"}},
            headers=_auth(test_user.id),
        )
    body = r.json()
    assert "error" not in body
    assert body["result"]["isError"] is True


@pytest.mark.asyncio
async def test_unknown_tool_is_invalid_params(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "nope", "_meta": _meta()}},
            headers=_modern_headers(test_user.id, "tools/call", name="nope"),
        )
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_mcp_name_may_arrive_base64_encoded(test_user, fake_tool_run):
    """Servers MUST decode a `=?base64?…?=` Mcp-Name before comparing it."""
    encoded = "=?base64?" + base64.b64encode(b"list_categories").decode() + "?="
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "list_categories", "_meta": _meta()}},
            headers=_modern_headers(test_user.id, "tools/call", name=encoded),
        )
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is False


# --------------------------------------------------------------- server validation


async def _post_modern(user_id, *, headers_override=None, meta=None, method="tools/list", params_extra=None):
    params = {"_meta": meta if meta is not None else _meta(), **(params_extra or {})}
    headers = _modern_headers(user_id, method)
    if headers_override is not None:
        headers.update(headers_override)
        for k, v in list(headers.items()):
            if v is None:
                del headers[k]
    async with _client() as cli:
        return await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 42, "method": method, "params": params}, headers=headers)


@pytest.mark.asyncio
async def test_missing_protocol_version_header_is_a_header_mismatch(test_user):
    r = await _post_modern(test_user.id, headers_override={"MCP-Protocol-Version": None})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_header_and_body_versions_must_match(test_user):
    r = await _post_modern(test_user.id, headers_override={"MCP-Protocol-Version": "2025-11-25"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_unsupported_version_lists_what_is_supported(test_user):
    """UnsupportedProtocolVersionError: 400, -32022, data.supported + data.requested."""
    r = await _post_modern(
        test_user.id,
        headers_override={"MCP-Protocol-Version": "1900-01-01"},
        meta=_meta("1900-01-01"),
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == -32022
    assert err["data"]["requested"] == "1900-01-01"
    assert MODERN in err["data"]["supported"]
    assert r.json()["id"] == 42


@pytest.mark.asyncio
async def test_missing_mcp_method_header_is_a_header_mismatch(test_user):
    r = await _post_modern(test_user.id, headers_override={"Mcp-Method": None})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_mcp_method_header_must_match_the_body(test_user):
    r = await _post_modern(test_user.id, headers_override={"Mcp-Method": "server/discover"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_tools_call_requires_a_matching_mcp_name(test_user):
    missing = await _post_modern(test_user.id, method="tools/call", params_extra={"name": "list_categories"})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == -32020

    wrong = await _post_modern(
        test_user.id, method="tools/call", params_extra={"name": "list_categories"},
        headers_override={"Mcp-Name": "list_accounts"},
    )
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == -32020


@pytest.mark.asyncio
async def test_missing_client_capabilities_is_invalid_params_with_400(test_user):
    """A request missing a required `_meta` field is malformed: -32602, HTTP 400."""
    meta = _meta()
    del meta["io.modelcontextprotocol/clientCapabilities"]
    r = await _post_modern(test_user.id, meta=meta)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_modern_header_without_body_meta_is_malformed(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=_modern_headers(test_user.id, "tools/list"),
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_unknown_modern_method_is_404_with_a_json_rpc_body(test_user):
    """404 + -32601, so a client can tell a modern server from a legacy 404."""
    r = await _post_modern(test_user.id, method="resources/list")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_initialize_is_not_a_modern_method(test_user):
    """2026-07-28 removed the handshake; a modern-shaped initialize is unknown."""
    r = await _post_modern(test_user.id, method="initialize")
    assert r.status_code == 404


# --------------------------------------------------------------- transport


@pytest.mark.asyncio
async def test_notifications_get_202_and_no_body(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_auth(test_user.id),
        )
    assert r.status_code == 202
    assert r.content == b""


@pytest.mark.asyncio
async def test_get_and_delete_are_405(test_user):
    """The GET stream and DELETE session termination were removed."""
    async with _client() as cli:
        get = await cli.get("/mcp", headers=_auth(test_user.id))
        delete = await cli.delete("/mcp", headers=_auth(test_user.id))
    assert get.status_code == 405
    assert delete.status_code == 405


@pytest.mark.asyncio
async def test_no_session_id_is_ever_minted_or_echoed(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
            headers={**_modern_headers(test_user.id, "server/discover"), "Mcp-Session-Id": "old-client-session"},
        )
    assert r.status_code == 200
    assert "mcp-session-id" not in r.headers


@pytest.mark.asyncio
async def test_a_foreign_origin_is_refused_with_403(test_user):
    """Servers MUST validate Origin (DNS rebinding) and 403 an invalid one —
    checked before auth, so a valid token from a hostile page still fails."""
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
            headers={**_modern_headers(test_user.id, "server/discover"), "Origin": "https://evil.example"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_an_explicitly_allowed_origin_passes(test_user, monkeypatch):
    from app.agents.config import get_agent_settings

    get_agent_settings.cache_clear()
    monkeypatch.setenv("AGENTS_MCP_ALLOWED_ORIGINS", "https://app.example.com")
    try:
        async with _client() as cli:
            r = await cli.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
                headers={**_modern_headers(test_user.id, "server/discover"), "Origin": "https://app.example.com"},
            )
        assert r.status_code == 200
    finally:
        get_agent_settings.cache_clear()


@pytest.mark.asyncio
async def test_request_id_must_not_be_null(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": None, "method": "tools/list"},
            headers=_auth(test_user.id),
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_a_client_sent_response_is_refused(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "result": {}}, headers=_auth(test_user.id))
    assert r.status_code == 400


# --------------------------------------------------------------- legacy era


@pytest.mark.asyncio
async def test_headerless_request_still_works_for_old_clients(test_user):
    """The in-app agent sent bare requests before this change; during a
    rolling deploy an old backend can still be talking to a new server."""
    async with _client() as cli:
        r = await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=_auth(test_user.id))
    assert r.status_code == 200
    tool = r.json()["result"]["tools"][0]
    assert "_securo" in tool, "legacy responses keep the field old runtimes read"
    assert "resultType" not in r.json()["result"]


@pytest.mark.asyncio
async def test_legacy_initialize_echoes_a_supported_version(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            headers=_auth(test_user.id),
        )
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.asyncio
async def test_legacy_initialize_offers_latest_legacy_for_an_unknown_request(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            headers=_auth(test_user.id),
        )
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.asyncio
async def test_legacy_ping_is_answered(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"}, headers=_auth(test_user.id))
    assert r.status_code == 200
    assert r.json()["result"] == {}


@pytest.mark.asyncio
async def test_legacy_request_with_an_unsupported_version_header_is_refused(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "ping"},
            headers={**_auth(test_user.id), "MCP-Protocol-Version": "1999-01-01"},
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32022
