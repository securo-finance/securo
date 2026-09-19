"""Tests for the Redis-backed OAuth state store."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import oauth_state


class _FakeRedis:
    """In-process Redis stand-in supporting set/getdel."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_redis():
    redis = _FakeRedis()
    with patch.object(oauth_state, "get_redis", AsyncMock(return_value=redis)):
        yield redis


@pytest.mark.asyncio
async def test_store_then_consume_round_trips(fake_redis):
    payload = {
        "user_id": "u1",
        "workspace_id": "w1",
        "provider": "enable_banking",
        "flow_params": {"country": "DE", "institution_name": "Revolut"},
    }
    state = await oauth_state.store_state(payload)
    assert isinstance(state, str) and len(state) > 20

    consumed = await oauth_state.consume_state(state)
    assert consumed is not None
    assert consumed["user_id"] == "u1"
    assert consumed["flow_params"]["country"] == "DE"
    assert "created_at" in consumed


@pytest.mark.asyncio
async def test_consume_is_one_shot(fake_redis):
    state = await oauth_state.store_state({"user_id": "u"})
    first = await oauth_state.consume_state(state)
    second = await oauth_state.consume_state(state)
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_consume_unknown_state_returns_none(fake_redis):
    assert await oauth_state.consume_state("does-not-exist") is None


@pytest.mark.asyncio
async def test_consume_empty_state_returns_none(fake_redis):
    assert await oauth_state.consume_state("") is None


@pytest.mark.asyncio
async def test_alias_state_lets_nonce_consume_the_same_payload(fake_redis):
    """Wealth Reader may return nonce instead of Securo's state."""
    state = await oauth_state.store_state({"user_id": "u", "provider": "wealthreader"})
    await oauth_state.alias_state(state, "wr-nonce")
    consumed = await oauth_state.consume_state("wr-nonce")
    assert consumed is not None
    assert consumed["provider"] == "wealthreader"
    assert "_oauth_siblings" not in consumed
    assert await oauth_state.consume_state(state) is None


@pytest.mark.asyncio
async def test_consume_canonical_invalidates_nonce_alias(fake_redis):
    state = await oauth_state.store_state({"user_id": "u", "provider": "wealthreader"})
    await oauth_state.alias_state(state, "wr-nonce")
    consumed = await oauth_state.consume_state(state)
    assert consumed is not None
    assert await oauth_state.consume_state("wr-nonce") is None


@pytest.mark.asyncio
async def test_alias_state_noops_on_empty_identical_or_missing(fake_redis):
    state = await oauth_state.store_state({"user_id": "u"})
    await oauth_state.alias_state(state, "")
    await oauth_state.alias_state("", "alias")
    await oauth_state.alias_state(state, state)
    await oauth_state.alias_state("does-not-exist", "ghost")
    assert await oauth_state.consume_state("ghost") is None
    assert await oauth_state.consume_state("") is None
    leftover = await oauth_state.consume_state(state)
    assert leftover is not None
    assert leftover["user_id"] == "u"
