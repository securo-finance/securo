"""Knowledge document upload + search.

Upload path:
  1. Save raw bytes to disk under AGENTS_KNOWLEDGE_STORAGE_PATH.
  2. Insert KnowledgeDoc with status="pending".
  3. Dispatch Celery task to parse → chunk → embed asynchronously.

Search path:
  1. Embed the query.
  2. Cosine-distance similarity search against agent's chunks (pgvector).
  3. Filter by similarity_threshold, sort by score, top_n.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import get_agent_settings
from app.agents.models.knowledge import KnowledgeChunk, KnowledgeDoc


def _storage_dir() -> str:
    base = get_agent_settings().knowledge_storage_path
    os.makedirs(base, exist_ok=True)
    return base


def _disk_path(doc_id: uuid.UUID, filename: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-80:]
    return os.path.join(_storage_dir(), f"{doc_id}__{safe}")


async def list_docs(session: AsyncSession, agent_id: uuid.UUID) -> list[KnowledgeDoc]:
    return list(
        (
            await session.execute(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.agent_id == agent_id)
                .order_by(KnowledgeDoc.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_doc(
    session: AsyncSession, doc_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[KnowledgeDoc]:
    return (
        await session.execute(
            select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id, KnowledgeDoc.user_id == user_id)
        )
    ).scalar_one_or_none()


async def upload_doc(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    mime: str,
    payload: bytes,
    pinned: bool = False,
) -> KnowledgeDoc:
    settings = get_agent_settings()
    if len(payload) > settings.knowledge_max_file_size_mb * 1024 * 1024:
        raise ValueError(f"file exceeds {settings.knowledge_max_file_size_mb} MB limit")

    doc = KnowledgeDoc(
        agent_id=agent_id,
        user_id=user_id,
        title=filename,
        source=filename,
        mime=mime,
        size_bytes=len(payload),
        status="pending",
        pinned=pinned,
    )
    session.add(doc)
    await session.flush()  # need doc.id to build the disk path
    path = _disk_path(doc.id, filename)
    with open(path, "wb") as fh:
        fh.write(payload)
    doc.storage_path = path
    await session.commit()
    await session.refresh(doc)
    return doc


async def delete_doc(session: AsyncSession, doc_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    doc = await get_doc(session, doc_id, user_id)
    if doc is None:
        return False
    path = doc.storage_path
    await session.delete(doc)
    await session.commit()
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return True


async def set_pinned(
    session: AsyncSession, doc_id: uuid.UUID, user_id: uuid.UUID, pinned: bool
) -> Optional[KnowledgeDoc]:
    doc = await get_doc(session, doc_id, user_id)
    if doc is None:
        return None
    doc.pinned = pinned
    await session.commit()
    await session.refresh(doc)
    return doc


async def replace_chunks(
    session: AsyncSession,
    *,
    doc_id: uuid.UUID,
    agent_id: uuid.UUID,
    chunks: list[tuple[str, list[float]]],
    embedding_model: str,
) -> int:
    """Replace any existing chunks for this doc with new (content, embedding)
    pairs. Used by the Celery ingest task."""
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))
    for ordinal, (content, embedding) in enumerate(chunks):
        session.add(
            KnowledgeChunk(
                doc_id=doc_id,
                agent_id=agent_id,
                ordinal=ordinal,
                content=content,
                embedding=embedding,
                embedding_model=embedding_model,
            )
        )
    await session.commit()
    return len(chunks)


async def mark_status(
    session: AsyncSession,
    doc_id: uuid.UUID,
    *,
    status: str,
    error: Optional[str] = None,
    chunk_count: Optional[int] = None,
) -> None:
    doc = (
        await session.execute(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))
    ).scalar_one_or_none()
    if doc is None:
        return
    doc.status = status
    # On success, always clear any stale error from prior attempts. Without
    # this the UI keeps showing "embed failed: …" alongside a green ready
    # badge, which is confusing.
    if status == "ready":
        doc.error = None
    elif error is not None:
        doc.error = error
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    await session.commit()


async def similarity_search(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    query_embedding: list[float],
    top_n: int = 6,
    similarity_threshold: float = 0.0,
) -> list[dict[str, Any]]:
    """Run cosine-distance ANN search. Lower distance = closer match.
    Returns rows with `score` = 1 - distance (so higher = better)."""
    distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
    q = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.doc_id,
            KnowledgeChunk.ordinal,
            KnowledgeChunk.content,
            distance.label("distance"),
        )
        .where(KnowledgeChunk.agent_id == agent_id)
        .order_by(distance.asc())
        .limit(int(top_n) * 3)  # over-fetch then threshold-filter
    )
    rows = (await session.execute(q)).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        score = 1.0 - float(r.distance)
        if score < similarity_threshold:
            continue
        out.append(
            {
                "id": str(r.id),
                "doc_id": str(r.doc_id),
                "ordinal": int(r.ordinal),
                "content": r.content,
                "score": score,
            }
        )
        if len(out) >= top_n:
            break
    return out


async def list_pinned_chunks(
    session: AsyncSession, *, agent_id: uuid.UUID, max_chunks: int = 20
) -> list[dict[str, Any]]:
    q = (
        select(
            KnowledgeChunk.id, KnowledgeChunk.doc_id, KnowledgeChunk.ordinal, KnowledgeChunk.content
        )
        .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
        .where(KnowledgeChunk.agent_id == agent_id, KnowledgeDoc.pinned.is_(True))
        .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.ordinal)
        .limit(max_chunks)
    )
    rows = (await session.execute(q)).all()
    return [
        {"id": str(r.id), "doc_id": str(r.doc_id), "ordinal": int(r.ordinal), "content": r.content}
        for r in rows
    ]


def hash_payload(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_size_limit_mb() -> int:
    return get_agent_settings().knowledge_max_file_size_mb
