"""Native embedder dispatch tests.

We do NOT actually download or run fastembed in CI — these tests stub
the underlying _get_native_embedder factory so the dispatch logic can
be exercised without the 120MB model download.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = pytest.mark.asyncio


async def test_native_embedder_is_lazy_imported_only_on_call():
    """Importing the embedding module must NOT pull in fastembed. Only
    the actual _embed_native call should. This protects users who don't
    use the agents feature from paying the import + memory cost."""
    import sys

    # Reset any prior fastembed import to test cleanly.
    if "fastembed" in sys.modules:
        del sys.modules["fastembed"]

    # Importing the embedding module by itself must not load fastembed.
    from app.agents.services import embedding  # noqa: F401

    assert "fastembed" not in sys.modules, (
        "fastembed must not be eagerly imported — wrap the import inside the function body"
    )


async def test_native_dispatch_calls_lazy_factory():
    """When provider=native, embed_texts must route to _embed_native and
    receive the dimension-adjusted vectors. We stub the factory."""
    from app.agents.services import embedding

    class _FakeEmbedder:
        def embed(self, texts):
            for _ in texts:
                yield [0.1, 0.2, 0.3, 0.4]  # 4-dim — far below target 1536

    with (
        patch.object(embedding, "_get_native_embedder", return_value=_FakeEmbedder()),
        patch.object(embedding.get_agent_settings(), "embedding_provider", "native"),
    ):
        # The lru_cache on _get_native_embedder caches the FAKE for the
        # rest of this session — fine because it's a fresh fake each test.
        embedding._get_native_embedder.cache_clear()
        with patch.object(embedding, "_get_native_embedder", return_value=_FakeEmbedder()):
            vecs, label = await embedding.embed_texts(["hello", "world"])

    assert len(vecs) == 2
    assert label.startswith("native:")
    # Each vector dimension-adjusted to AGENTS_EMBEDDING_DIM (1536).
    for v in vecs:
        assert len(v) == 1536
        # First 4 dims are the fake values; the rest are zero-padded.
        assert v[:4] == [0.1, 0.2, 0.3, 0.4]
        assert all(x == 0.0 for x in v[4:])


async def test_remote_dispatch_when_provider_is_not_native():
    """provider=ollama (or openai/openai_compatible) routes to the LLM
    provider abstraction's embed() method — not the native path."""
    from unittest.mock import AsyncMock
    from app.agents.services import embedding

    fake_provider = AsyncMock()
    fake_provider.embed = AsyncMock(return_value=[[1.0] * 768])

    with (
        patch.object(embedding, "_build_remote_provider", return_value=fake_provider),
        patch.object(embedding.get_agent_settings(), "embedding_provider", "ollama"),
    ):
        vecs, label = await embedding.embed_texts(["hi"])

    assert len(vecs) == 1
    assert len(vecs[0]) == 1536  # padded
    assert label.startswith("ollama:")
    fake_provider.embed.assert_awaited_once()


async def test_empty_input_short_circuits():
    """No texts → no work, no provider import, no remote call."""
    from app.agents.services import embedding

    vecs, _ = await embedding.embed_texts([])
    assert vecs == []


async def test_dimension_padding_preserves_signal():
    """Padding with zeros must not destroy the front of the vector — it
    just rounds out to the target dim. Cosine similarity is unaffected
    because zero dims contribute nothing to the dot product."""
    from app.agents.services.embedding import _adjust_dim

    assert _adjust_dim([0.5, -0.3], 4) == [0.5, -0.3, 0.0, 0.0]
    assert _adjust_dim([0.1] * 1600, 1536) == [0.1] * 1536  # truncated
    assert _adjust_dim([1.0, 2.0, 3.0], 3) == [1.0, 2.0, 3.0]
