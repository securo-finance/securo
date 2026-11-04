"""Cover app/agents/tasks/ingest._do_ingest, bypassing the Celery layer.

We drive the async helper directly with our SQLite test session_maker
plus a monkeypatched embed_texts (so we don't pull ONNX / fastembed
into the test env). The KnowledgeChunk.embedding column is shimmed to
JSON in conftest, so writing a Python list works on SQLite.
"""
from __future__ import annotations

import tempfile
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.services import knowledge_service
from app.agents.tasks.ingest import _do_ingest


@pytest.fixture(autouse=True)
def _storage_in_tmpdir(monkeypatch):
    """Redirect AGENTS_KNOWLEDGE_STORAGE_PATH to a per-test tmpdir. The
    default `/app/data/agent_knowledge` is not writable in CI."""
    tmp = tempfile.mkdtemp(prefix="kb-ingest-")
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "knowledge_storage_path", tmp)
    yield tmp


def _session_maker_from(session):
    """Build a session_maker that points at the same SQLite engine the
    test session already uses. _do_ingest opens its own session inside,
    which is exactly the contract we want to test."""
    return async_sessionmaker(session.bind, expire_on_commit=False)


@pytest.mark.asyncio
async def test_do_ingest_happy_path_writes_chunks_and_marks_ready(
    session, test_agent, test_user, monkeypatch
):
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="ok.txt",
        mime="text/plain",
        payload=b"one two three four",
    )

    monkeypatch.setattr(
        "app.agents.services.chunking.chunks_from_upload",
        lambda payload, mime, title: ["chunk-a", "chunk-b"],
    )

    async def fake_embed(chunks):
        # 1536-dim vector full of zeros — matches embedding_dim, harmless on SQLite.
        return [[0.0] * 1536 for _ in chunks], "fake-model"

    monkeypatch.setattr("app.agents.services.embedding.embed_texts", fake_embed)

    result = await _do_ingest(_session_maker_from(session), doc.id, test_agent.id)

    assert result == {"ok": True, "chunks": 2}
    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    # The session_maker inside _do_ingest commits — we need a fresh read.
    await session.refresh(refreshed)
    assert refreshed.status == "ready"
    assert refreshed.chunk_count == 2
    assert refreshed.error is None


@pytest.mark.asyncio
async def test_do_ingest_missing_doc_returns_reason(session, test_agent):
    """A doc_id that doesn't exist should short-circuit, not crash."""
    result = await _do_ingest(_session_maker_from(session), uuid.uuid4(), test_agent.id)
    assert result == {"ok": False, "reason": "doc missing"}


@pytest.mark.asyncio
async def test_do_ingest_marks_failed_when_storage_unreadable(
    session, test_agent, test_user, monkeypatch
):
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="x.txt",
        mime="text/plain",
        payload=b"x",
    )
    # Point storage_path at a non-existent file — Path.read_bytes raises.
    doc.storage_path = "/tmp/this-file-really-shouldnt-exist-12345.bin"
    await session.commit()

    result = await _do_ingest(_session_maker_from(session), doc.id, test_agent.id)
    assert result == {"ok": False, "reason": "read failed"}

    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.status == "failed"
    assert "read failed" in (refreshed.error or "")


@pytest.mark.asyncio
async def test_do_ingest_marks_failed_when_no_extractable_text(
    session, test_agent, test_user, monkeypatch
):
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="empty.txt",
        mime="text/plain",
        payload=b"",
    )
    monkeypatch.setattr(
        "app.agents.services.chunking.chunks_from_upload",
        lambda payload, mime, title: [],
    )

    result = await _do_ingest(_session_maker_from(session), doc.id, test_agent.id)
    assert result == {"ok": False, "reason": "no text"}

    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.status == "failed"
    assert "no extractable text" in (refreshed.error or "")
    assert refreshed.chunk_count == 0


@pytest.mark.asyncio
async def test_do_ingest_marks_failed_when_embedding_fails(
    session, test_agent, test_user, monkeypatch
):
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="boom.txt",
        mime="text/plain",
        payload=b"some text",
    )
    monkeypatch.setattr(
        "app.agents.services.chunking.chunks_from_upload",
        lambda payload, mime, title: ["a chunk"],
    )

    async def boom(chunks):
        raise RuntimeError("provider 503")

    monkeypatch.setattr("app.agents.services.embedding.embed_texts", boom)

    result = await _do_ingest(_session_maker_from(session), doc.id, test_agent.id)
    assert result == {"ok": False, "reason": "embed failed"}

    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.status == "failed"
    assert "embed failed" in (refreshed.error or "")
    assert "provider 503" in (refreshed.error or "")
