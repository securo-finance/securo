from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models.agent import Agent, AgentTool
from app.agents.models.conversation import Conversation
from app.agents.models.knowledge import KnowledgeDoc
from app.agents.schemas.agent import AgentCreate, AgentUpdate


async def list_agents(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[Agent]:
    q = select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.created_at.desc())
    if not include_archived:
        q = q.where(Agent.is_archived.is_(False))
    rows = list((await session.execute(q)).scalars().all())
    # One scalar query per (conv, kb) so the agents list page can show
    # counts without N+1. Cheap on small fan-out; if agent counts grow
    # we should switch to a single GROUP BY join.
    if rows:
        ids = [a.id for a in rows]
        conv_counts = dict(
            (r[0], r[1])
            for r in (
                await session.execute(
                    select(Conversation.agent_id, func.count(Conversation.id))
                    .where(Conversation.agent_id.in_(ids))
                    .group_by(Conversation.agent_id)
                )
            ).all()
        )
        kb_counts = dict(
            (r[0], r[1])
            for r in (
                await session.execute(
                    select(KnowledgeDoc.agent_id, func.count(KnowledgeDoc.id))
                    .where(KnowledgeDoc.agent_id.in_(ids))
                    .group_by(KnowledgeDoc.agent_id)
                )
            ).all()
        )
        # Stash on the model instances; pydantic AgentRead reads them.
        for a in rows:
            a.conversation_count = int(conv_counts.get(a.id, 0))  # type: ignore[attr-defined]
            a.knowledge_count = int(kb_counts.get(a.id, 0))  # type: ignore[attr-defined]
    return rows


async def get_agent(
    session: AsyncSession,
    agent_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Optional[Agent]:
    return (await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.workspace_id == workspace_id)
    )).scalar_one_or_none()


async def create_agent(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: AgentCreate,
) -> Agent:
    agent = Agent(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        icon=data.icon,
        color=data.color,
        connection_id=data.connection_id,
        provider=data.provider,
        model=data.model,
        temperature=data.temperature,
        max_history_messages=data.max_history_messages,
        top_n=data.top_n,
        similarity_threshold=data.similarity_threshold,
        extra=data.extra or {},
        auto_context=data.auto_context,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(
    session: AsyncSession,
    agent_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: AgentUpdate,
) -> Optional[Agent]:
    agent = await get_agent(session, agent_id, workspace_id)
    if agent is None:
        return None
    payload = data.model_dump(exclude_unset=True)
    # If turning this agent into the default, clear the flag on every
    # other agent in the same workspace first — the partial unique
    # index would otherwise reject the commit.
    if payload.get("is_default") is True:
        await session.execute(
            update(Agent)
            .where(
                Agent.workspace_id == workspace_id,
                Agent.id != agent_id,
                Agent.is_default.is_(True),
            )
            .values(is_default=False)
        )
    for field, value in payload.items():
        setattr(agent, field, value)
    await session.commit()
    await session.refresh(agent)
    return agent


async def get_default_agent(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[Agent]:
    """The default agent is what the global slide-over chat panel uses.
    Falls back to the most-recently-created non-archived agent so the
    panel still works for workspaces that haven't picked one yet."""
    explicit = (await session.execute(
        select(Agent).where(
            Agent.workspace_id == workspace_id,
            Agent.is_default.is_(True),
            Agent.is_archived.is_(False),
        )
    )).scalar_one_or_none()
    if explicit is not None:
        return explicit
    return (await session.execute(
        select(Agent)
        .where(Agent.workspace_id == workspace_id, Agent.is_archived.is_(False))
        .order_by(Agent.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def delete_agent(
    session: AsyncSession, agent_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    agent = await get_agent(session, agent_id, workspace_id)
    if agent is None:
        return False
    await session.delete(agent)
    await session.commit()
    return True


async def list_tools(session: AsyncSession, agent_id: uuid.UUID) -> list[AgentTool]:
    return list((await session.execute(
        select(AgentTool).where(AgentTool.agent_id == agent_id)
    )).scalars().all())


async def set_tool_enabled(
    session: AsyncSession, agent_id: uuid.UUID, server: str, tool_name: str, enabled: bool
) -> AgentTool:
    existing = (await session.execute(
        select(AgentTool).where(
            AgentTool.agent_id == agent_id,
            AgentTool.server == server,
            AgentTool.tool_name == tool_name,
        )
    )).scalar_one_or_none()
    if existing is None:
        existing = AgentTool(agent_id=agent_id, server=server, tool_name=tool_name, enabled=enabled)
        session.add(existing)
    else:
        existing.enabled = enabled
    await session.commit()
    return existing


async def allowed_tool_pairs(session: AsyncSession, agent_id: uuid.UUID) -> Optional[set[tuple[str, str]]]:
    """Returns the set of (server, tool_name) pairs the agent is permitted
    to call. Returns None when no rows exist — interpreted as 'allow all'
    so first-run agents work without setup."""
    rows = await list_tools(session, agent_id)
    if not rows:
        return None
    return {(r.server, r.tool_name) for r in rows if r.enabled}


async def replace_tool_enablement(
    session: AsyncSession, agent_id: uuid.UUID, items: list[tuple[str, str, bool]]
) -> None:
    await session.execute(delete(AgentTool).where(AgentTool.agent_id == agent_id))
    for server, name, enabled in items:
        session.add(AgentTool(agent_id=agent_id, server=server, tool_name=name, enabled=enabled))
    await session.commit()
