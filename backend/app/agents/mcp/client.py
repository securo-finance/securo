"""Minimal JSON-RPC 2.0 MCP client used by the agent runtime.

Talks to one or more MCP servers (Securo's built-in + any user-supplied).
Per call, mints a short-lived JWT scoped to (user_id, conversation_id).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.agents.config import get_agent_settings
from app.agents.mcp.auth import mint_token
from app.agents.providers.base import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class ToolHandle:
    """One discovered tool, with the server it belongs to and its schema."""
    server: str
    name: str
    description: str
    parameters: dict[str, Any]
    is_proposal: bool = False


@dataclass
class _ServerSpec:
    name: str
    url: str


def _parse_servers() -> list[_ServerSpec]:
    s = get_agent_settings()
    out = [_ServerSpec(name="securo", url=s.builtin_mcp_url)]
    extra = (s.extra_mcp_servers or "").strip()
    if extra:
        for raw in extra.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if "|" in raw:
                url, name = raw.split("|", 1)
            else:
                url, name = raw, raw
            out.append(_ServerSpec(name=name.strip(), url=url.strip()))
    return out


MODERN_PROTOCOL_VERSION = "2026-07-28"
CLIENT_INFO = {"name": "securo-agent-runtime", "version": "0.2.0"}
# `_meta` key the built-in server puts its per-tool extras under. Mirrors
# mcp_server.protocol.TOOL_META_KEY; not imported, because this module also
# talks to third-party servers and must not depend on the server package.
_SECURO_TOOL_META_KEY = "com.usesecuro/tool"

# JSON-RPC error codes a *modern* (2026-07-28+) server uses to refuse a
# request it did understand. Seeing one means "fix the request", never
# "fall back to the legacy protocol".
_MODERN_REFUSAL_CODES = frozenset({-32020, -32021, -32022})

_HEADER_SAFE = re.compile(r"^[\x20-\x7e\t]*$")


def _encode_header_value(value: str) -> str:
    """The spec's `=?base64?…?=` sentinel for values unsafe as a header."""
    if _HEADER_SAFE.match(value) and value == value.strip() and not (
        value.startswith("=?base64?") and value.endswith("?=")
    ):
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def _is_modern_refusal(status: int, data: Any) -> bool:
    """Whether a 4xx body identifies a modern MCP server.

    Per the 2026-07-28 backward-compatibility rules, a dual-era client that
    gets 400/404/405 inspects the body: a recognised modern JSON-RPC error
    means the server is modern and the request should be corrected; anything
    else — an empty body, HTML, a bare framework error — means a legacy
    server, and the client falls back.
    """
    if not isinstance(data, dict) or not isinstance(data.get("error"), dict):
        return False
    code = data["error"].get("code")
    if code in _MODERN_REFUSAL_CODES:
        return True
    # 404 + Method not found, and 400 + Invalid params (missing `_meta`),
    # are how a modern server answers; a legacy server does not send them.
    return (status == 404 and code == -32601) or (status == 400 and code == -32602)


def _parse_sse(text: str, request_id: Any) -> Any:
    """Pull the JSON-RPC response for `request_id` out of an SSE body.

    A modern server may answer any request with a request-scoped event
    stream: optional notifications, then the response. The built-in server
    never streams, but a user-supplied one can.
    """
    data_lines: list[str] = []
    found: Any = None

    def flush() -> None:
        nonlocal found
        if not data_lines:
            return
        try:
            message = json.loads("\n".join(data_lines))
        except ValueError:
            message = None
        data_lines.clear()
        if isinstance(message, dict) and message.get("id") == request_id and (
            "result" in message or "error" in message
        ):
            found = message

    for line in text.splitlines():
        if line == "":
            flush()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    flush()
    return found


class MCPClient:
    """Single-server JSON-RPC client. One instance per server URL.

    Dual-era, as the 2026-07-28 spec recommends for clients that must work
    with servers of either generation: every request is first sent in the
    modern shape (`_meta` protocol fields plus the mirrored `MCP-Protocol-
    Version`, `Mcp-Method` and `Mcp-Name` headers). A 4xx that is not a
    modern refusal marks the server legacy, and the request is resent in the
    original plain shape. The era is remembered per instance so the probe
    happens once, not on every call.
    """

    def __init__(self, *, name: str, url: str):
        self.name = name
        self.url = url
        self._next_id = 0
        # None until the first exchange settles which protocol era the
        # server speaks; then "modern" or "legacy".
        self._era: Optional[str] = None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _post(self, method: str, params: dict[str, Any], *, token: str) -> Any:
        if self._era != "legacy":
            status, data = await self._send_modern(method, params, token=token)
            if status < 400:
                self._era = "modern"
                return self._unwrap(data, modern=True)
            if _is_modern_refusal(status, data):
                self._era = "modern"
                raise RuntimeError(f"MCP {self.name} error: {data['error']}")
            if status not in (400, 404, 405):
                # Auth failures, server errors: not an era signal.
                raise RuntimeError(f"MCP {self.name} HTTP {status}")
            self._era = "legacy"
        return await self._send_legacy(method, params, token=token)

    async def _send_modern(self, method: str, params: dict[str, Any], *, token: str) -> tuple[int, Any]:
        request_id = self._id()
        body_params = dict(params)
        body_params["_meta"] = {
            **(params.get("_meta") or {}),
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if method == "tools/call" and isinstance(params.get("name"), str):
            headers["Mcp-Name"] = _encode_header_value(params["name"])
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
            content_type = (getattr(resp, "headers", None) or {}).get("content-type", "")
            if "text/event-stream" in content_type:
                return resp.status_code, _parse_sse(resp.text, request_id)
            try:
                data = resp.json()
            except ValueError:
                data = None
            return resp.status_code, data

    async def _send_legacy(self, method: str, params: dict[str, Any], *, token: str) -> Any:
        payload = {"jsonrpc": "2.0", "id": self._id(), "method": method, "params": params}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return self._unwrap(data, modern=False)

    def _unwrap(self, data: Any, *, modern: bool) -> Any:
        if not isinstance(data, dict):
            raise RuntimeError(f"MCP {self.name} returned no JSON-RPC response")
        if "error" in data and data["error"]:
            raise RuntimeError(f"MCP {self.name} error: {data['error']}")
        result = data.get("result")
        if modern and isinstance(result, dict) and result.get("resultType") == "input_required":
            # Multi round-trip requests (the server asking the client for
            # input mid-call) are not something the agent runtime can answer.
            raise RuntimeError(f"MCP {self.name} asked for client input, which is not supported")
        return result

    async def list_tools(self, *, token: str) -> list[ToolHandle]:
        result = await self._post("tools/list", {}, token=token)
        out: list[ToolHandle] = []
        for t in (result or {}).get("tools", []):
            meta = t.get("_meta") if isinstance(t.get("_meta"), dict) else {}
            extras = meta.get(_SECURO_TOOL_META_KEY) or t.get("_securo") or {}
            out.append(ToolHandle(
                server=self.name,
                name=t.get("name") or "",
                description=t.get("description") or "",
                parameters=t.get("inputSchema") or {"type": "object", "properties": {}},
                is_proposal=bool(extras.get("is_proposal", False)),
            ))
        return out

    async def call_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        result = await self._post("tools/call", {"name": name, "arguments": arguments}, token=token)
        # Prefer structuredContent when present (our server emits both).
        if isinstance(result, dict) and "structuredContent" in result:
            return {
                "ok": not bool(result.get("isError")),
                "data": result.get("structuredContent"),
                "text": _join_text(result.get("content")),
            }
        return {"ok": not bool((result or {}).get("isError")), "data": result, "text": ""}


def _join_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
    return "\n".join(parts)


class MCPRegistry:
    """Aggregates tools from multiple MCP servers and routes calls. The
    namespacing convention is `<server>.<tool>` to avoid collisions when
    two servers expose tools with the same name.
    """

    def __init__(self):
        self._servers: dict[str, MCPClient] = {}
        for spec in _parse_servers():
            self._servers[spec.name] = MCPClient(name=spec.name, url=spec.url)

    def server_names(self) -> list[str]:
        return list(self._servers.keys())

    async def discover(
        self,
        *,
        user_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID] = None,
        conversation_id: Optional[uuid.UUID] = None,
        agent_id: Optional[uuid.UUID] = None,
    ) -> list[ToolHandle]:
        token = mint_token(
            user_id=user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )
        out: list[ToolHandle] = []
        for client in self._servers.values():
            try:
                tools = await client.list_tools(token=token)
            except Exception as exc:
                # A server that can't be reached costs the agent every tool it
                # exposes, and the chat still answers, just without any data.
                # Say so in the log: otherwise the only symptom is an assistant
                # claiming it can't see anything.
                logger.warning(
                    "MCP server %r unreachable at %s (%s). Its tools are "
                    "unavailable for this conversation.",
                    client.name,
                    client.url,
                    exc,
                )
                continue
            out.extend(tools)
        return out

    @staticmethod
    def to_provider_tools(handles: list[ToolHandle], *, allowed: Optional[set[tuple[str, str]]] = None) -> list[ToolDefinition]:
        """Convert MCP tool handles into provider-agnostic ToolDefinition.

        `allowed` is a set of (server, tool_name) pairs. When None, all are
        passed through. Tool name on the wire is `<server>__<name>` so the
        server can be recovered from the LLM's tool call.
        """
        out: list[ToolDefinition] = []
        for h in handles:
            if allowed is not None and (h.server, h.name) not in allowed:
                continue
            out.append(ToolDefinition(
                name=f"{h.server}__{h.name}",
                description=h.description,
                parameters=h.parameters or {"type": "object", "properties": {}},
            ))
        return out

    async def call(
        self,
        *,
        wire_name: str,
        arguments: dict[str, Any],
        user_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID] = None,
        conversation_id: Optional[uuid.UUID] = None,
        agent_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        # Pass agent_id so per-agent tools (search_knowledge_base) can
        # scope their results. Without this, MCP-side ctx.agent_id is
        # None and the knowledge tool refuses.
        token = mint_token(
            user_id=user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )

        # Happy path: namespaced name (server__tool).
        if "__" in wire_name:
            server, tool_name = wire_name.split("__", 1)
            client = self._servers.get(server)
            if client is not None:
                return await client.call_tool(name=tool_name, arguments=arguments, token=token)

        # Fallback: many LLMs drop the namespace prefix and emit just the
        # bare tool name. Resolve by scanning every registered server.
        bare = wire_name.split("__", 1)[-1]
        for server_name, client in self._servers.items():
            try:
                handles = await client.list_tools(token=token)
            except Exception as exc:
                logger.warning(
                    "MCP server %r unreachable at %s (%s) while resolving tool %r.",
                    server_name,
                    client.url,
                    exc,
                    wire_name,
                )
                continue
            if any(h.name == bare for h in handles):
                return await client.call_tool(name=bare, arguments=arguments, token=token)

        raise ValueError(f"unknown tool: {wire_name}")
