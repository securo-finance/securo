from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas.conversation import ConversationRead, MessageRead
from app.agents.services import agent_service, conversation_service
from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class RenameConversationBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


_TITLE_PROMPT = (
    "Generate a very short title (max 6 words, no punctuation, no quotes) "
    "summarizing the topic of this conversation. Return ONLY the title text "
    "with no preamble, no explanation, no quotes."
)


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    agent_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await conversation_service.list_conversations(
        session, ctx.workspace.id, agent_id=agent_id, limit=limit
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    conv = await conversation_service.get_conversation(session, conversation_id, ctx.workspace.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=500),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    conv = await conversation_service.get_conversation(session, conversation_id, ctx.workspace.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return await conversation_service.list_messages(session, conversation_id, limit=limit)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: RenameConversationBody,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    conv = await conversation_service.update_title(
        session, conversation_id, ctx.workspace.id, body.title
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.post("/conversations/{conversation_id}/generate-title", response_model=ConversationRead)
async def generate_title(
    conversation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Ask the conversation's agent's LLM to summarize the chat into a
    short title. Persists the result and returns the updated row. Best-
    effort: if the LLM call fails, the existing title is left in place
    and a 200 is still returned with whatever was there before."""
    from app.agents.providers.base import ChatMessage
    from app.agents.runtime.executor import _provider_and_model_for

    conv = await conversation_service.get_conversation(session, conversation_id, ctx.workspace.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    msgs = await conversation_service.list_messages(session, conversation_id, limit=12)
    transcript = []
    for m in msgs:
        if m.role in ("user", "assistant") and m.content:
            transcript.append(f"{m.role.upper()}: {m.content[:600]}")
    if not transcript:
        return conv  # nothing to summarize yet

    agent = await agent_service.get_agent(session, conv.agent_id, ctx.workspace.id)
    if agent is None:
        return conv

    try:
        provider, model = await _provider_and_model_for(session, agent)
        if not model:
            return conv
        prompt_messages = [
            ChatMessage(role="system", content=_TITLE_PROMPT),
            ChatMessage(role="user", content="\n".join(transcript)),
        ]
        resp = await provider.chat(prompt_messages, model=model, temperature=0.2, max_tokens=40)
        content = (resp.content or "").strip()
        # Some local models emit reasoning tags before the answer; strip
        # those FIRST so the answer line isn't accidentally swallowed.
        for marker in ("</think>", "</reasoning>"):
            if marker in content:
                content = content.split(marker, 1)[1]
        content = content.strip().strip('"').strip("'")
        # First non-empty line wins.
        candidate = next(
            (line.strip().strip('"').strip("'") for line in content.splitlines() if line.strip()),
            "",
        )
        if candidate:
            conv = await conversation_service.update_title(
                session, conversation_id, ctx.workspace.id, candidate[:80]
            )
    except Exception:  # noqa: BLE001
        logger.exception("title generation failed for conversation %s", conversation_id)
    return conv


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ok = await conversation_service.delete_conversation(session, conversation_id, ctx.workspace.id)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
