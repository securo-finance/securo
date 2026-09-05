"""Minimal JSON-RPC 2.0 MCP client used by the agent runtime.

Talks to one or more MCP servers (Securo's built-in + any user-supplied).
Per call, mints a short-lived JWT scoped to (user_id, conversation_id).
"""
from __future__ import annotations

import logging
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


class MCPClient:
    """Single-server JSON-RPC client. One instance per server URL."""

    def __init__(self, *, name: str, url: str):
        self.name = name
        self.url = url
        self._next_id = 0

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _post(self, method: str, params: dict[str, Any], *, token: str) -> Any:
        payload = {"jsonrpc": "2.0", "id": self._id(), "method": method, "params": params}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data and data["error"]:
            raise RuntimeError(f"MCP {self.name} error: {data['error']}")
        return data.get("result")

    async def list_tools(self, *, token: str) -> list[ToolHandle]:
        result = await self._post("tools/list", {}, token=token)
        out: list[ToolHandle] = []
        for t in (result or {}).get("tools", []):
            extras = t.get("_securo") or {}
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
