from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services import agent_service, knowledge_service
from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize(doc) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "agent_id": str(doc.agent_id),
        "title": doc.title,
        "source": doc.source,
        "mime": doc.mime,
        "size_bytes": doc.size_bytes,
        "status": doc.status,
        "error": doc.error,
        "chunk_count": doc.chunk_count,
        "pinned": doc.pinned,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.get("/{agent_id}/knowledge")
async def list_knowledge(
    agent_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    agent = await agent_service.get_agent(session, agent_id, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    docs = await knowledge_service.list_docs(session, agent_id)
    return {"items": [_serialize(d) for d in docs], "total": len(docs)}


@router.post("/{agent_id}/knowledge", status_code=status.HTTP_201_CREATED)
async def upload_knowledge(
    agent_id: uuid.UUID,
    file: UploadFile = File(...),
    pinned: bool = Form(False),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    agent = await agent_service.get_agent(session, agent_id, ctx.workspace.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    payload = await file.read()
    try:
        doc = await knowledge_service.upload_doc(
            session,
            agent_id=agent_id,
            user_id=ctx.user_id,
            filename=file.filename or "document",
            mime=file.content_type or "application/octet-stream",
            payload=payload,
            pinned=pinned,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    # Fire-and-forget Celery dispatch — task does the parsing/embedding.
    try:
        from app.worker import celery_app

        celery_app.send_task(
            "app.agents.tasks.ingest.ingest_doc", args=[str(doc.id), str(agent_id)]
        )
    except Exception:  # noqa: BLE001 — embedding failure shouldn't kill the upload
        await knowledge_service.mark_status(
            session, doc.id, status="failed", error="celery dispatch failed"
        )

    return _serialize(doc)


@router.patch("/{agent_id}/knowledge/{doc_id}/pin")
async def toggle_pin(
    agent_id: uuid.UUID,
    doc_id: uuid.UUID,
    pinned: bool,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    doc = await knowledge_service.set_pinned(session, doc_id, ctx.user_id, pinned)
    if doc is None:
        raise HTTPException(status_code=404, detail="doc not found")
    return _serialize(doc)


@router.delete("/{agent_id}/knowledge/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    agent_id: uuid.UUID,
    doc_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ok = await knowledge_service.delete_doc(session, doc_id, ctx.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="doc not found")
