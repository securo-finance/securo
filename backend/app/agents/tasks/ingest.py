"""Celery task: parse → chunk → embed an uploaded knowledge document.

Registered with the existing celery_app. Only fires when AGENTS_ENABLED
is on, because the upload endpoint that dispatches it lives behind the
same flag.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.agents.tasks.ingest.ingest_doc", bind=True, max_retries=2, default_retry_delay=30
)
def ingest_doc(self, doc_id_str: str, agent_id_str: str) -> dict:
    """Sync entry point that runs the async ingest in a fresh loop."""
    return asyncio.run(_async_ingest(doc_id_str, agent_id_str))


async def _async_ingest(doc_id_str: str, agent_id_str: str) -> dict:
    doc_id = uuid.UUID(doc_id_str)
    agent_id = uuid.UUID(agent_id_str)

    # Create a fresh engine per task. Reusing the global async_session_maker
    # inside Celery prefork workers reuses asyncpg connections bound to
    # event loops that asyncio.run() has already closed, raising "another
    # operation is in progress" on the next task. NullPool ensures the
    # engine doesn't cache anything beyond this task.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await _do_ingest(session_maker, doc_id, agent_id)
    finally:
        await engine.dispose()


async def _do_ingest(session_maker, doc_id: uuid.UUID, agent_id: uuid.UUID) -> dict:
    from app.agents.models.knowledge import KnowledgeDoc
    from app.agents.services import knowledge_service
    from app.agents.services.chunking import chunks_from_upload
    from app.agents.services.embedding import embed_texts
    from sqlalchemy import select

    async with session_maker() as session:
        await knowledge_service.mark_status(session, doc_id, status="processing")
        doc = (
            await session.execute(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))
        ).scalar_one_or_none()
        if doc is None or not doc.storage_path:
            return {"ok": False, "reason": "doc missing"}

        try:
            payload = Path(doc.storage_path).read_bytes()
        except Exception as exc:  # noqa: BLE001
            await knowledge_service.mark_status(
                session, doc_id, status="failed", error=f"read failed: {exc}"
            )
            return {"ok": False, "reason": "read failed"}

        chunks = list(chunks_from_upload(payload, doc.mime, doc.title))
        if not chunks:
            await knowledge_service.mark_status(
                session, doc_id, status="failed", error="no extractable text", chunk_count=0
            )
            return {"ok": False, "reason": "no text"}

        try:
            embeddings, model_label = await embed_texts(chunks)
        except Exception as exc:  # noqa: BLE001
            logger.exception("embedding failed for doc %s", doc_id)
            await knowledge_service.mark_status(
                session, doc_id, status="failed", error=f"embed failed: {exc}"
            )
            return {"ok": False, "reason": "embed failed"}

        n = await knowledge_service.replace_chunks(
            session,
            doc_id=doc_id,
            agent_id=agent_id,
            chunks=list(zip(chunks, embeddings)),
            embedding_model=model_label,
        )
        await knowledge_service.mark_status(
            session, doc_id, status="ready", error=None, chunk_count=n
        )
        return {"ok": True, "chunks": n}
