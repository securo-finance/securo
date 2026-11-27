"""Cover the doc-metadata paths of app/agents/services/knowledge_service.

Skipping similarity_search / replace_chunks / list_pinned_chunks: those
hit a pgvector column type that doesn't exist on SQLite. The rest of the
file (upload, list, get, delete, set_pinned, mark_status, hash_payload,
file_size_limit_mb) is platform-agnostic.
"""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from app.agents.services import knowledge_service


@pytest.fixture(autouse=True)
def _storage_in_tmpdir(monkeypatch):
    """Redirect AGENTS_KNOWLEDGE_STORAGE_PATH to a per-test tmpdir so
    uploads don't leak into other tests or pollute /app/data."""
    tmp = tempfile.mkdtemp(prefix="kb-test-")
    from app.agents.config import get_agent_settings

    # Settings is @lru_cache'd — monkeypatch the attribute on the
    # cached instance.
    s = get_agent_settings()
    monkeypatch.setattr(s, "knowledge_storage_path", tmp)
    yield tmp


@pytest.mark.asyncio
async def test_upload_doc_writes_file_and_persists_metadata(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="note.md",
        mime="text/markdown",
        payload=b"# Hello",
    )
    assert doc.id is not None
    assert doc.status == "pending"
    assert doc.size_bytes == len(b"# Hello")
    assert doc.storage_path
    # The file is actually on disk.
    assert os.path.exists(doc.storage_path)
    with open(doc.storage_path, "rb") as fh:
        assert fh.read() == b"# Hello"


@pytest.mark.asyncio
async def test_upload_doc_sanitizes_disk_filename(session, test_agent, test_user, _storage_in_tmpdir):
    """Slashes and ../ in the filename cannot escape the storage root."""
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="../etc/passwd",
        mime="text/plain",
        payload=b"x",
    )

    assert doc.storage_path is not None
    assert os.path.dirname(doc.storage_path) == _storage_in_tmpdir
    assert os.path.basename(doc.storage_path) == f"{doc.id}__.._etc_passwd"


@pytest.mark.asyncio
async def test_get_doc_scopes_by_user(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="x.txt", mime="text/plain", payload=b"x",
    )
    # Wrong user → None.
    other = uuid.uuid4()
    assert await knowledge_service.get_doc(session, doc.id, other) is None
    # Right user → returns the row.
    found = await knowledge_service.get_doc(session, doc.id, test_user.id)
    assert found is not None
    assert found.id == doc.id


@pytest.mark.asyncio
async def test_delete_doc_removes_row_and_file(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="delete-me.txt", mime="text/plain", payload=b"hi",
    )
    path = doc.storage_path

    assert path is not None
    assert os.path.exists(path)
    ok = await knowledge_service.delete_doc(session, doc.id, test_user.id)
    assert ok is True
    assert not os.path.exists(path)
    assert await knowledge_service.get_doc(session, doc.id, test_user.id) is None


@pytest.mark.asyncio
async def test_delete_doc_swallows_missing_file_on_disk(session, test_agent, test_user):
    """If someone removes the file under us, the DB row should still
    delete cleanly."""
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="ghost.txt", mime="text/plain", payload=b"x",
    )

    assert doc.storage_path is not None
    os.remove(doc.storage_path)
    assert await knowledge_service.delete_doc(session, doc.id, test_user.id) is True


@pytest.mark.asyncio
async def test_mark_status_ready_clears_prior_error(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="s.txt", mime="text/plain", payload=b"x",
    )
    # Initial failure → error message stored.
    await knowledge_service.mark_status(session, doc.id, status="failed", error="embed failed: 500")
    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    assert refreshed.status == "failed"
    assert "embed failed" in (refreshed.error or "")
    # Subsequent success → status flips AND prior error cleared.
    await knowledge_service.mark_status(session, doc.id, status="ready", chunk_count=4)
    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    assert refreshed.status == "ready"
    assert refreshed.error is None
    assert refreshed.chunk_count == 4


@pytest.mark.asyncio
async def test_mark_status_unknown_doc_is_silent_noop(session):
    # No exception even when the doc doesn't exist — used by the
    # Celery task to avoid crashing on a missing row.
    await knowledge_service.mark_status(session, uuid.uuid4(), status="failed", error="x")
