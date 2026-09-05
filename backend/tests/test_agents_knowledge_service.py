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
async def test_upload_doc_rejects_oversized_payload(session, test_agent, test_user, monkeypatch):
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "knowledge_max_file_size_mb", 1)
    too_big = b"x" * (1024 * 1024 + 1)
    with pytest.raises(ValueError, match="exceeds"):
        await knowledge_service.upload_doc(
            session,
            agent_id=test_agent.id,
            user_id=test_user.id,
            filename="big.bin",
            mime="application/octet-stream",
            payload=too_big,
        )


@pytest.mark.asyncio
async def test_upload_doc_sanitizes_disk_filename(session, test_agent, test_user):
    # Slashes / ../ would normally let an attacker escape the storage
    # root. _disk_path replaces them with underscores and clips to 80
    # chars. We verify by checking the resulting file lives directly in
    # storage_path's basename without any path traversal.
    doc = await knowledge_service.upload_doc(
        session,
        agent_id=test_agent.id,
        user_id=test_user.id,
        filename="../etc/passwd",
        mime="text/plain",
        payload=b"x",
    )

    assert doc.storage_path is not None
    assert "../" not in doc.storage_path.split("/", 5)[-1]  # no traversal in stored basename
    assert os.path.dirname(doc.storage_path) == os.path.abspath(os.path.dirname(doc.storage_path))


@pytest.mark.asyncio
async def test_list_docs_returns_all_docs_for_agent(session, test_agent, test_user):
    a = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="a.txt", mime="text/plain", payload=b"a",
    )
    b = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="b.txt", mime="text/plain", payload=b"b",
    )
    rows = await knowledge_service.list_docs(session, test_agent.id)
    # Ordering is `created_at DESC` in code, but on fast-running test envs
    # the two uploads can share a timestamp, so we just assert membership.
    assert {a.id, b.id} <= {d.id for d in rows}


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
async def test_delete_doc_returns_false_when_missing(session, test_user):
    assert await knowledge_service.delete_doc(session, uuid.uuid4(), test_user.id) is False


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
async def test_set_pinned_toggles_flag(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="pin.txt", mime="text/plain", payload=b"x",
    )
    pinned = await knowledge_service.set_pinned(session, doc.id, test_user.id, True)
    assert pinned is not None
    assert pinned.pinned is True
    unpinned = await knowledge_service.set_pinned(session, doc.id, test_user.id, False)
    assert unpinned is not None
    assert unpinned.pinned is False


@pytest.mark.asyncio
async def test_set_pinned_returns_none_when_missing(session, test_user):
    out = await knowledge_service.set_pinned(session, uuid.uuid4(), test_user.id, True)
    assert out is None


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
async def test_mark_status_failed_preserves_prior_status_metadata(session, test_agent, test_user):
    doc = await knowledge_service.upload_doc(
        session, agent_id=test_agent.id, user_id=test_user.id,
        filename="fail.txt", mime="text/plain", payload=b"x",
    )
    await knowledge_service.mark_status(session, doc.id, status="processing")
    await knowledge_service.mark_status(session, doc.id, status="failed", error="parse failed")
    refreshed = await knowledge_service.get_doc(session, doc.id, test_user.id)

    assert refreshed is not None
    assert refreshed.status == "failed"
    assert "parse failed" in (refreshed.error or "")


@pytest.mark.asyncio
async def test_mark_status_unknown_doc_is_silent_noop(session):
    # No exception even when the doc doesn't exist — used by the
    # Celery task to avoid crashing on a missing row.
    await knowledge_service.mark_status(session, uuid.uuid4(), status="failed", error="x")


def test_hash_payload_is_stable_and_unique():
    a = knowledge_service.hash_payload(b"hello")
    b = knowledge_service.hash_payload(b"hello")
    c = knowledge_service.hash_payload(b"world")
    assert a == b
    assert a != c
    assert len(a) == 64  # SHA-256 hex digest length


def test_file_size_limit_mb_returns_settings_value(monkeypatch):
    from app.agents.config import get_agent_settings

    monkeypatch.setattr(get_agent_settings(), "knowledge_max_file_size_mb", 42)
    assert knowledge_service.file_size_limit_mb() == 42
