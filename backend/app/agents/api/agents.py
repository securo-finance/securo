from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.mcp.client import MCPRegistry
from app.agents.schemas.agent import AgentCreate, AgentRead, AgentToolToggle, AgentUpdate
from app.agents.services import agent_service
from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentRead])
async def list_agents(
    include_archived: bool = False,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await agent_service.list_agents(
        session, ctx.workspace.id, include_archived=include_archived
    )


@router.get("/default", response_model=AgentRead)
async def get_default_agent(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Used by the global slide-over chat panel. Returns the workspace's
    flagged default agent, or its most-recently-created one as a fallback."""
    agent = await agent_service.get_default_agent(session, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="no agent available")
    return agent


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    data: AgentCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    discovered = None
    if data.tools is None:
        mcp = MCPRegistry()
        handles = await mcp.discover(user_id=ctx.user_id, workspace_id=ctx.workspace.id)
        discovered = mcp.complete_pairs(handles)
    return await agent_service.create_agent(
        session, ctx.workspace.id, ctx.user_id, data, discovered=discovered
    )


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    agent = await agent_service.get_agent(session, agent_id, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    agent = await agent_service.update_agent(session, agent_id, ctx.workspace.id, data)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ok = await agent_service.delete_agent(session, agent_id, ctx.workspace.id)
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")


@router.get("/{agent_id}/tools")
async def get_agent_tools(
    agent_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Discover all tools from registered MCP servers and merge with the
    per-agent enablement state. Returns shape:
      { servers: [{name}], tools: [{server, name, description, enabled, is_proposal}] }
    """
    agent = await agent_service.get_agent(session, agent_id, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    mcp = MCPRegistry()
    handles = await mcp.discover(user_id=ctx.user_id, workspace_id=ctx.workspace.id)
    discovered = mcp.complete_pairs(handles)
    if discovered is not None:
        await agent_service.initialize_tools(session, agent, discovered)
    allowed = await agent_service.allowed_tool_pairs(session, agent_id)
    tools = [
        {
            "server": h.server,
            "name": h.name,
            "description": h.description,
            "is_proposal": h.is_proposal,
            # Not initialized only while a server is unreachable: the agent
            # still runs with what it finds, as it did before (see executor).
            "enabled": (h.server, h.name) in allowed or not agent.tools_initialized,
        }
        for h in handles
    ]
    return {"servers": [{"name": s} for s in mcp.server_names()], "tools": tools}


@router.put("/{agent_id}/tools")
async def put_agent_tools(
    agent_id: uuid.UUID,
    items: list[AgentToolToggle],
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    agent = await agent_service.get_agent(session, agent_id, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await agent_service.replace_tool_enablement(
        session, agent_id, [(t.server, t.tool_name, t.enabled) for t in items]
    )
    return {"updated": len(items)}
