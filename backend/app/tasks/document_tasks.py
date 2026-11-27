"""Celery task: read one uploaded document (ADR 0037).

Dispatched only by the document routes, which answer 404 while
DOCUMENT_AI_ENABLED is off, so an instance without the feature never runs it.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.row_security import maker_for_row
from app.worker import celery_app


@celery_app.task(name="app.tasks.document_tasks.extract_document")
def extract_document(extraction_id: str) -> None:
    asyncio.run(_extract(uuid.UUID(extraction_id)))


async def _extract(extraction_id: uuid.UUID) -> None:
    from app.services.document_extraction_service import run_extraction

    # A fresh engine per task: a pooled asyncpg connection is bound to the
    # event loop that created it, and asyncio.run() closes that loop.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        maker = await maker_for_row(session_maker, "document_extractions", extraction_id)
        async with maker() as session:
            await run_extraction(session, extraction_id)
    finally:
        await engine.dispose()
