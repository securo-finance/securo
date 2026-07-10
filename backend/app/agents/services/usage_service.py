"""LLM usage logging.

Every provider call (chat or embedding) writes one row to `agent_llm_usage`.
Drives the per-user cost dashboard and any future rate-limiting.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models.usage import LlmUsage


# Rough per-1M-token USD pricing for cost estimates. Inaccurate by design —
# prices change weekly and this is just a budget ballpark for the user. Out
# of date entries default to 0 (logged for tokens but no cost shown).
_PRICING_PER_M = {
    # OpenAI
    ("openai", "gpt-4o"): (2.50, 10.00),
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("openai", "gpt-4-turbo"): (10.0, 30.0),
    ("openai", "gpt-3.5-turbo"): (0.50, 1.50),
    ("openai", "text-embedding-3-small"): (0.02, 0.0),
    ("openai", "text-embedding-3-large"): (0.13, 0.0),
    # Anthropic
    ("anthropic", "claude-opus-4-5"): (15.0, 75.0),
    ("anthropic", "claude-sonnet-4-5"): (3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"): (0.25, 1.25),
}


def estimate_cost_usd(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> Optional[float]:
    rates = _PRICING_PER_M.get((provider, model))
    if rates is None:
        return None
    in_rate, out_rate = rates
    return round((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 6)


async def record_usage(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    agent_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    message_id: Optional[uuid.UUID],
    provider: str,
    model: str,
    kind: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: Optional[int] = None,
) -> LlmUsage:
    cost = estimate_cost_usd(provider, model, input_tokens, output_tokens)
    row = LlmUsage(
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        message_id=message_id,
        provider=provider,
        model=model,
        kind=kind,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cost_usd=cost,
        latency_ms=latency_ms,
    )
    session.add(row)
    await session.commit()
    return row
