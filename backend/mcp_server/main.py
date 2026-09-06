"""MCP server FastAPI app. JSON-RPC 2.0 over HTTP POST /mcp.

Exposes Securo's built-in tools (read-only + propose-mutations) over the
Model Context Protocol. Runs as a separate container; gated by the
`agents` profile in docker-compose.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.database import async_session_maker
from mcp_server import tools as _tools_pkg  # noqa: F401  triggers tool registration
from mcp_server.auth import verify_request
from mcp_server.registry import REGISTRY, call_tool, list_tools

logger = logging.getLogger(__name__)

# The routes live on a router, not directly on an app, so an all-in-one
# deployment can include them in the main API process while the dedicated
# mcp-server container keeps running `uvicorn mcp_server.main:app` unchanged.
# Paths stay absolute — `POST /mcp` is the contract the Helm ingress and the
# agent runtime depend on, and mounting this as a sub-app would have made it
# /mcp/mcp.
router = APIRouter()


SERVER_INFO = {
    "name": "securo-builtin",
    "version": "0.1.0",
}
PROTOCOL_VERSION = "2024-11-05"


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "tools": len(REGISTRY)}


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request) -> JSONResponse:
    # Auth first — never accept unauthenticated calls.
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
        return JSONResponse(status_code=400, content=_err(None, -32700, "parse error"))

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_err(None, -32600, "invalid request"))

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return JSONResponse(status_code=400, content=_err(req_id, -32600, "invalid request"))

    if method == "initialize":
        return JSONResponse(
            content=_ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        )

    if method == "tools/list":
        return JSONResponse(content=_ok(req_id, {"tools": list_tools()}))

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return JSONResponse(content=_err(req_id, -32602, "tools/call requires 'name'"))
        try:
            async with async_session_maker() as session:
                result = await call_tool(session, ctx, name, arguments)
            # MCP wraps tool output in `content` blocks. Use the structured
            # variant — many clients (and our own runtime) prefer JSON.
            return JSONResponse(content=_ok(req_id, {
                "content": [{"type": "text", "text": _safe_json(result)}],
                "structuredContent": result,
                "isError": False,
            }))
        except KeyError as exc:
            return JSONResponse(content=_err(req_id, -32601, str(exc)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP tool failure: %s", name)
            return JSONResponse(content=_ok(req_id, {
                "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                "isError": True,
            }))

    return JSONResponse(content=_err(req_id, -32601, f"unknown method: {method}"))


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


# Standalone app for the dedicated mcp-server container and the Railway
# service. `uvicorn mcp_server.main:app` is unaffected by the refactor above.
app = FastAPI(title="Securo MCP Server", openapi_url=None, docs_url=None)
app.include_router(router)
