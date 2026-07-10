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


def list_tools() -> list[dict[str, Any]]:
    """MCP-compatible tool list payload."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "inputSchema": s.parameters,
            "_securo": {"is_proposal": s.is_proposal, "tags": s.tags},
        }
        for s in REGISTRY.values()
    ]


async def call_tool(
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    arguments: dict[str, Any] | None,
) -> Any:
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown tool: {name}")
    return await spec.handler(session=session, ctx=ctx, **(arguments or {}))
