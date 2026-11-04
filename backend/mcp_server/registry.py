"""Tool registry for the MCP server.

Each tool is a Python coroutine registered via the @tool decorator. The
registry holds (name → ToolSpec) for /mcp's `tools/list` and `tools/call`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.auth import CallContext


ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)
    handler: ToolHandler
    # Optional. When True, the tool produces a preview (no DB writes); the
    # frontend asks the user to confirm before applying. Drives UI hints.
    is_proposal: bool = False
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    description: str,
    parameters: dict[str, Any],
    is_proposal: bool = False,
    tags: list[str] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator. The handler must be an async function with signature
    `async def handler(session: AsyncSession, ctx: CallContext, **kwargs)`.
    """
    def deco(fn: ToolHandler) -> ToolHandler:
        if name in REGISTRY:
            raise RuntimeError(f"duplicate tool registration: {name}")
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            is_proposal=is_proposal,
            tags=list(tags or []),
        )
        return fn
    return deco


def list_tools(*, legacy: bool = True) -> list[dict[str, Any]]:
    """MCP tool list payload, in registration order (the spec asks for a
    deterministic order so clients can cache the list).

    Securo's per-tool extras travel under a vendor-prefixed `_meta` key, the
    place the spec gives them. Legacy responses also keep the old top-level
    `_securo` object: during a rolling deploy the backend's agent runtime can
    briefly be one version behind the mcp-server, and it read that field.
    Modern responses leave it out, since `Tool` defines no such property.
    """
    from mcp_server.protocol import TOOL_META_KEY, tool_annotations

    out: list[dict[str, Any]] = []
    for s in REGISTRY.values():
        extras = {"is_proposal": s.is_proposal, "tags": s.tags}
        item: dict[str, Any] = {
            "name": s.name,
            "description": s.description,
            "inputSchema": s.parameters,
            "annotations": tool_annotations(s.name, is_proposal=s.is_proposal),
            "_meta": {TOOL_META_KEY: extras},
        }
        if legacy:
            item["_securo"] = extras
        out.append(item)
    return out


async def call_tool(
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    arguments: dict[str, Any] | None,
) -> Any:
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown tool: {name}")
    from app.core.app_clock import use_timezone
    from mcp_server.tools._helpers import resolve_workspace_id

    # A token minted before workspaces existed carries no `ws_id`; the tool
    # resolves the default workspace itself, and a caller with none at all
    # still gets the application timezone rather than an error here.
    try:
        workspace_id = await resolve_workspace_id(session, ctx)
    except ValueError:
        workspace_id = None
    async with use_timezone(session, workspace_id):
        return await spec.handler(session=session, ctx=ctx, **(arguments or {}))
