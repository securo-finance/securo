"""Embedding helper.

Centralizes the choice of embedding provider+model and the dimension
adjustment (since pgvector columns are fixed-dimension and we lock to
`AGENTS_EMBEDDING_DIM`).

Providers:
  - `native`: in-process via fastembed (default). Multilingual MiniLM
    by default. Lazy-imports fastembed on first use so users not on the
    agents feature pay zero memory/import cost.
  - `ollama`: external Ollama embedding model.
  - `openai`: OpenAI's text-embedding-* endpoints.
  - `openai_compatible`: any OpenAI-shaped /v1/embeddings endpoint.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Optional

from app.agents.config import get_agent_settings
from app.agents.providers.base import LLMNotSupportedError
from app.agents.providers.registry import build_provider

logger = logging.getLogger(__name__)


def _adjust_dim(vec: list[float], target: int) -> list[float]:
    """Pad with zeros or truncate to match the column dimension."""
    if len(vec) == target:
        return list(vec)
    if len(vec) > target:
        return list(vec[:target])
    out = list(vec)
    out.extend([0.0] * (target - len(vec)))
    return out


@functools.lru_cache(maxsize=4)
def _get_native_embedder(model_name: str, cache_dir: str) -> Any:
    """Lazy-import fastembed and instantiate the model exactly once per
    (model, cache_dir). The first call downloads the ONNX model to
    `cache_dir` (~120 MB for the default multilingual MiniLM); subsequent
    calls hit the in-memory instance.

    Importing fastembed is intentionally inside this function — modules
    using `app.agents.services.embedding` don't drag fastembed in unless
    they actually trigger embedding (i.e. only the agents feature does).
    """
    from fastembed import TextEmbedding  # heavy: onnxruntime + numpy

    logger.info("loading native embedder %s (cache=%s)", model_name, cache_dir)
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


def _embed_native_sync(texts: list[str], model_name: str, cache_dir: str) -> list[list[float]]:
    """Synchronous embed call. Runs in a thread via run_in_executor so
    we don't block the event loop on what's effectively CPU + I/O work."""
    emb = _get_native_embedder(model_name, cache_dir)
    return [list(map(float, v)) for v in emb.embed(texts)]


async def _embed_native(texts: list[str]) -> tuple[list[list[float]], str]:
    s = get_agent_settings()
    loop = asyncio.get_running_loop()
    vectors = await loop.run_in_executor(
        None, _embed_native_sync, texts, s.embedding_model, s.embedding_native_cache_dir
    )
    return vectors, f"native:{s.embedding_model}"


def _build_remote_provider():
    s = get_agent_settings()
    name = s.embedding_provider
    if name == "ollama":
        return build_provider("ollama", base_url=s.embedding_ollama_base_url)
    if name == "openai":
        return build_provider(
            "openai", api_key=s.embedding_openai_api_key, base_url=s.embedding_openai_base_url
        )
    if name == "openai_compatible":
        return build_provider(
            "openai_compatible",
            api_key=s.embedding_openai_api_key,
            base_url=s.embedding_openai_base_url,
        )
    raise LLMNotSupportedError(f"embedding provider '{name}' has no embedding endpoint")


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Returns (vectors, model_label). Vectors are dimension-adjusted to
    `AGENTS_EMBEDDING_DIM`."""
    if not texts:
        return [], get_agent_settings().embedding_model
    s = get_agent_settings()
    target = s.embedding_dim
    if s.embedding_provider == "native":
        raw, label = await _embed_native(texts)
    else:
        provider = _build_remote_provider()
        raw = await provider.embed(texts, model=s.embedding_model)
        label = f"{s.embedding_provider}:{s.embedding_model}"
    return ([_adjust_dim(v, target) for v in raw], label)


async def embed_one(text: str) -> Optional[list[float]]:
    vecs, _ = await embed_texts([text])
    return vecs[0] if vecs else None
