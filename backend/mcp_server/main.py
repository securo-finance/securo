"""MCP server FastAPI app: Streamable HTTP on a single `POST /mcp` endpoint.

Exposes Securo's built-in tools (read-only + propose-mutations) over the
Model Context Protocol. Runs as a separate container; gated by the
`agents` profile in docker-compose.

Dual-era per the 2026-07-28 specification (see `mcp_server/protocol.py`):
modern requests carry their protocol version in `params._meta` and are
served statelessly; requests without it follow the legacy `initialize`
lifecycle. Responses are always single JSON objects — the spec lets a server
choose JSON over a per-request SSE stream, and no tool here streams progress.
There is no GET stream and no session: both were removed in 2026-07-28, so
GET/DELETE get 405 and no `Mcp-Session-Id` is ever issued.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.agents.config import get_agent_settings
from app.core.database import async_session_maker
from mcp_server import protocol as p
from mcp_server import tools as _tools_pkg  # noqa: F401  triggers tool registration
from mcp_server.auth import CallContext, verify_request
from mcp_server.registry import REGISTRY, call_tool, list_tools

logger = logging.getLogger(__name__)

app = FastAPI(title="Securo MCP Server", openapi_url=None, docs_url=None)


SERVER_INFO = {
    "name": "securo-builtin",
    "version": "0.2.0",
}
# The capability set is the same in both eras: tools, and no list-changed
# notifications (the tool set only changes with a deploy).
CAPABILITIES = {"tools": {"listChanged": False}}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@app.get("/health")
async def health():
    return {"status": "ok", "tools": len(REGISTRY)}


@app.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_no_stream() -> Response:
    """2026-07-28 removed the standalone GET stream and session termination
    by DELETE. The spec asks a server that receives either to answer 405."""
    return Response(status_code=405, headers={"Allow": "POST"})


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    # DNS-rebinding guard before anything else, as the transport requires.
    allowed = p.parse_allowed_origins(get_agent_settings().mcp_allowed_origins)
    if not p.origin_allowed(request.headers.get("origin"), allowed):
        return JSONResponse(
            status_code=403,
            content={"jsonrpc": "2.0", "error": {"code": p.INVALID_REQUEST, "message": "Origin not allowed"}},
        )

    # Auth next — never accept unauthenticated calls.
    try:
        ctx = verify_request(request)
    except Exception as exc:  # HTTPException from verify_request
        status_code = getattr(exc, "status_code", 401)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse(
            status_code=status_code,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": str(detail)}},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_err(None, p.PARSE_ERROR, "parse error"))

    # One message per POST. JSON-RPC batching is not part of MCP.
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_err(None, p.INVALID_REQUEST, "invalid request"))

    method = body.get("method")
    if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
        # Also catches a JSON-RPC *response*, which clients must not send.
        return JSONResponse(status_code=400, content=_err(body.get("id"), p.INVALID_REQUEST, "invalid request"))

    if "id" not in body:
        # A notification. Nothing to act on here — the legacy
        # `notifications/initialized` included — but it must be accepted
        # with 202 and no body, never answered.
        return Response(status_code=202)

    req_id = body["id"]
    if isinstance(req_id, bool) or not isinstance(req_id, (str, int)):
        return JSONResponse(status_code=400, content=_err(None, p.INVALID_REQUEST, "request id must be a string or integer"))

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return JSONResponse(status_code=400, content=_err(req_id, p.INVALID_PARAMS, "params must be an object"))

    meta = params.get("_meta")
    header_version = request.headers.get("mcp-protocol-version")
    is_modern = (
        (isinstance(meta, dict) and p.META_PROTOCOL_VERSION in meta)
        or header_version in p.MODERN_VERSIONS
        or method == "server/discover"
    )

    try:
        if is_modern:
            status, payload = await _serve_modern(request, ctx, req_id, method, params, header_version)
        else:
            status, payload = await _serve_legacy(ctx, req_id, method, params, header_version)
    except p.ProtocolError as exc:
        return JSONResponse(status_code=exc.status, content=exc.body())
    return JSONResponse(status_code=status, content=payload)


# --------------------------------------------------------------------- modern


async def _serve_modern(
    request: Request,
    ctx: CallContext,
    req_id: Any,
    method: str,
    params: dict[str, Any],
    header_version: str | None,
) -> tuple[int, dict]:
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        raise p.ProtocolError(400, p.INVALID_PARAMS, "missing params._meta", request_id=req_id)
    body_version = meta.get(p.META_PROTOCOL_VERSION)
    if not isinstance(body_version, str):
        raise p.ProtocolError(400, p.INVALID_PARAMS, f"missing _meta {p.META_PROTOCOL_VERSION}", request_id=req_id)
    if not isinstance(meta.get(p.META_CLIENT_CAPABILITIES), dict):
        raise p.ProtocolError(400, p.INVALID_PARAMS, f"missing _meta {p.META_CLIENT_CAPABILITIES}", request_id=req_id)

    if header_version is None:
        raise p.header_mismatch("MCP-Protocol-Version header is required", req_id)
    if header_version != body_version:
        raise p.header_mismatch(
            f"MCP-Protocol-Version header value '{header_version}' does not match body value '{body_version}'",
            req_id,
        )
    if body_version not in p.MODERN_VERSIONS:
        raise p.unsupported_version(body_version, req_id)

    header_method = request.headers.get("mcp-method")
    if header_method is None:
        raise p.header_mismatch("Mcp-Method header is required", req_id)
    if header_method != method:
        raise p.header_mismatch(f"Mcp-Method header value '{header_method}' does not match body value '{method}'", req_id)

    if method not in p.MODERN_METHODS:
        # The JSON-RPC body is what tells a client this 404 comes from a
        # modern MCP server rather than a legacy HTTP+SSE one.
        return 404, _err(req_id, p.METHOD_NOT_FOUND, f"Method not found: {method}")

    result_meta = {p.META_SERVER_INFO: SERVER_INFO}

    if method == "server/discover":
        return 200, _ok(req_id, {
            "resultType": "complete",
            "supportedVersions": list(p.SUPPORTED_VERSIONS),
            "capabilities": CAPABILITIES,
            "ttlMs": p.LIST_TTL_MS,
            "cacheScope": p.LIST_CACHE_SCOPE,
            "_meta": result_meta,
        })

    if method == "tools/list":
        return 200, _ok(req_id, {
            "resultType": "complete",
            "tools": list_tools(legacy=False),
            "ttlMs": p.LIST_TTL_MS,
            "cacheScope": p.LIST_CACHE_SCOPE,
            "_meta": result_meta,
        })

    # tools/call
    name = params.get("name")
    raw_header_name = request.headers.get("mcp-name")
    if raw_header_name is None:
        raise p.header_mismatch("Mcp-Name header is required for tools/call", req_id)
    header_name = p.decode_header_value(raw_header_name)
    if header_name is None:
        raise p.header_mismatch("Mcp-Name header value contains invalid characters", req_id)
    if not isinstance(name, str) or header_name != name:
        raise p.header_mismatch(f"Mcp-Name header value '{header_name}' does not match body value '{name}'", req_id)

    return 200, await _call_tool(ctx, req_id, name, params.get("arguments"), result_meta=result_meta)


# --------------------------------------------------------------------- legacy


async def _serve_legacy(
    ctx: CallContext,
    req_id: Any,
    method: str,
    params: dict[str, Any],
    header_version: str | None,
) -> tuple[int, dict]:
    # A legacy client sends the version it negotiated on every request after
    # `initialize` (2025-06-18 and later). One this server never offered is
    # refused, and the modern error body lets a dual-era client re-select.
    if header_version is not None and header_version not in p.LEGACY_VERSIONS:
        raise p.unsupported_version(header_version, req_id)

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = requested if requested in p.LEGACY_VERSIONS else p.LATEST_LEGACY_VERSION
        return 200, _ok(req_id, {
            "protocolVersion": negotiated,
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
        })

    if method == "ping":
        return 200, _ok(req_id, {})

    if method == "tools/list":
        return 200, _ok(req_id, {"tools": list_tools(legacy=True)})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return 200, _err(req_id, p.INVALID_PARAMS, "tools/call requires 'name'")
        return 200, await _call_tool(ctx, req_id, name, params.get("arguments"), result_meta=None)

    return 200, _err(req_id, p.METHOD_NOT_FOUND, f"unknown method: {method}")


# --------------------------------------------------------------------- shared


async def _call_tool(
    ctx: CallContext,
    req_id: Any,
    name: str,
    arguments: Any,
    *,
    result_meta: dict | None,
) -> dict:
    """Run one tool. `result_meta` is set on the modern path only.

    An unknown tool is a protocol error (-32602), checked up front rather
    than inferred from a KeyError: a KeyError raised *inside* a tool is a
    bug in that tool, and reporting it as "unknown tool" would hide it. A
    tool that runs and fails is a tool execution error — a normal result
    with `isError: true` — so the model can read it and recover.
    """
    if name not in REGISTRY:
        return _err(req_id, p.INVALID_PARAMS, f"Unknown tool: {name}")
    if arguments is not None and not isinstance(arguments, dict):
        return _err(req_id, p.INVALID_PARAMS, "tools/call 'arguments' must be an object")

    try:
        async with async_session_maker() as session:
            result = await call_tool(session, ctx, name, arguments)
        # MCP wraps tool output in `content` blocks. Structured content is
        # returned too, with its JSON serialization as text for clients that
        # only read text, as the spec recommends.
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": _safe_json(result)}],
            "structuredContent": result,
            "isError": False,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("MCP tool failure: %s", name)
        payload = {
            "content": [{"type": "text", "text": f"Tool error: {exc}"}],
            "isError": True,
        }

    if result_meta is not None:
        payload = {"resultType": "complete", **payload, "_meta": result_meta}
    return _ok(req_id, payload)


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)
