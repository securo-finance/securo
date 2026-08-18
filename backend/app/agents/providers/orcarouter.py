from __future__ import annotations

from typing import Optional

from app.agents.providers.openai_compatible import OpenAICompatibleProvider


class OrcaRouterProvider(OpenAICompatibleProvider):
    """OrcaRouter (https://www.orcarouter.ai) — an OpenAI-compatible router
    exposing 200+ models (OpenAI, Anthropic, Google, DeepSeek, ...) behind a
    single API key.

    Same ``/v1/chat/completions`` wire format as OpenAI, so we inherit the
    OpenAI-compatible client and just preset the public endpoint. The base
    URL stays configurable for self-hosted or regional deployments; a
    connection without one uses the public ``https://api.orcarouter.ai/v1``.
    """

    name = "orcarouter"

    def __init__(self, *, api_key: str = "", base_url: str = "https://api.orcarouter.ai/v1", model: Optional[str] = None):
        super().__init__(api_key=api_key, base_url=base_url, model=model)
