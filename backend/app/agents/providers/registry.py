from __future__ import annotations

from typing import Optional

from app.agents.providers.anthropic import AnthropicProvider
from app.agents.providers.base import LLMProvider
from app.agents.providers.ollama import OllamaProvider
from app.agents.providers.openai import OpenAIProvider
from app.agents.providers.openai_compatible import OpenAICompatibleProvider
from app.agents.providers.orcarouter import OrcaRouterProvider


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "orcarouter": OrcaRouterProvider,
}


def list_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())


def build_provider(
    name: str,
    *,
    api_key: str = "",
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMProvider:
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {name}. Available: {list_providers()}")
    kwargs: dict = {"api_key": api_key, "model": model}
    if base_url:
        kwargs["base_url"] = base_url
    return cls(**{k: v for k, v in kwargs.items() if v is not None})
