"""Per-IP rate limiting and a length cap on the agent chat endpoint (#1591).

The chat route streams a reply and spends provider tokens on every POST, so
the ceiling has to apply on entry, before the stream opens. A turn is a single
POST: the tool calls the executor makes happen inside that same request, so a
turn with many tools never spends more than one slot here. Naming a
conversation calls the provider too, so it spends from the same bucket.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

from app.agents.schemas.conversation import SendMessageRequest
from app.core.config import DEFAULT_TRUSTED_PROXY_NETWORKS, Settings
from app.core.rate_limit import login_rate_limit
from app.main import app


def _shipped_default() -> int:
    """The default this repository ships, not whatever a developer's .env says."""
    return Settings.model_fields["agents_chat_rate_limit_per_minute"].default


@pytest.fixture(autouse=True)
def _mock_redis():
    """A Redis whose sorted-set count behaves like the limiter's real one.

    Overrides the suite-wide no-op mock: the limiter has to see how many
    requests already hit the same key, which the default one always reports
    as zero.
    """
    counters: dict[str, int] = {}

    def make_pipeline():
        pipe = MagicMock()
        seen = {"key": ""}

        def remember(key, *args, **kwargs):
            seen["key"] = key
            return pipe

        pipe.zremrangebyscore = remember
        pipe.zcard = lambda *a, **kw: pipe
        pipe.zadd = lambda *a, **kw: pipe
        pipe.expire = lambda *a, **kw: pipe

        async def execute():
            key = seen["key"]
            prior = counters.get(key, 0)
            counters[key] = prior + 1
            return [0, prior, True, True]

        pipe.execute = AsyncMock(side_effect=execute)
        return pipe

    mock = AsyncMock()
    mock.pipeline = make_pipeline
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    mock.delete = AsyncMock()

    async def _fake_get_redis():
        return mock

    with patch("app.core.redis.get_redis", _fake_get_redis), \
         patch("app.core.rate_limit.get_redis", _fake_get_redis), \
         patch("app.api.custom_auth.get_redis", _fake_get_redis), \
         patch("app.api.two_factor.get_redis", _fake_get_redis):
        yield counters


def _settings(limit: int, hops: int = 0) -> SimpleNamespace:
    """Settings as the limiter reads them: the ceiling, and the proxy trust."""
    return SimpleNamespace(
        trusted_proxy_hops=hops,
        trusted_proxy_networks=DEFAULT_TRUSTED_PROXY_NETWORKS,
        agents_chat_rate_limit_per_minute=limit,
    )


def _chat_url(agent_id: uuid.UUID | None = None) -> str:
    return f"/api/agents/{agent_id or uuid.uuid4()}/chat"


# ---------------------------------------------------------------------------
# Message length
# ---------------------------------------------------------------------------


def test_message_content_is_capped_at_4000_characters():
    SendMessageRequest(content="x" * 4000)
    with pytest.raises(ValidationError) as excinfo:
        SendMessageRequest(content="x" * 4001)
    assert "4000" in str(excinfo.value)


@pytest.mark.parametrize(
    ("content", "expected_in_message"),
    [
        pytest.param("x" * 4001, "4000", id="too-long"),
        pytest.param("", "at least 1 character", id="empty"),
    ],
)
async def test_invalid_message_content_is_refused_with_422(
    client: AsyncClient, auth_headers, content: str, expected_in_message: str
):
    response = await client.post(
        _chat_url(),
        json={"content": content},
        headers=auth_headers,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "content"]
    assert expected_in_message in detail[0]["msg"]


def test_page_context_is_capped_at_4000_characters():
    """The content cap is pointless if the blob that rides next to it into
    the same prompt is free: a megabyte of page context still costs tokens."""
    SendMessageRequest(content="oi", page_context={"summary": "x" * 3900})
    with pytest.raises(ValidationError) as excinfo:
        SendMessageRequest(content="oi", page_context={"summary": "x" * 5000})
    assert "page_context" in str(excinfo.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("page_context", {"summary": "x" * 5000}, id="page-context"),
        pytest.param("channel", "x" * 41, id="channel"),
    ],
)
async def test_oversized_side_fields_are_refused_with_422(
    client: AsyncClient, auth_headers, field: str, value
):
    response = await client.post(
        _chat_url(),
        json={"content": "oi", field: value},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def _generate_title_url(conversation_id: uuid.UUID | None = None) -> str:
    return (
        f"/api/agents/conversations/{conversation_id or uuid.uuid4()}/generate-title"
    )


async def test_generate_title_spends_the_same_budget_as_chat(client: AsyncClient):
    """Naming a conversation is another provider call, so it goes through the
    chat's limiter and its bucket: leaving it unmetered would give a caller a
    free path to the provider, and a bucket of its own would hand out a second
    minute the moment it switched route."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=2)):
        first = await client.post(_generate_title_url())
        second = await client.post(_generate_title_url())

        refused = await client.post(_generate_title_url())
        # Same bucket, so the chat is out of budget too.
        chat_refused = await client.post(_chat_url(), json={"content": "hi"})

    assert (first.status_code, second.status_code) == (401, 401)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert chat_refused.status_code == 429


async def test_chat_refusal_leaves_no_budget_for_naming_the_conversation(
    client: AsyncClient,
):
    """The other way round: an exhausted chat budget must also close the title
    route, whether the ceiling comes from chat turns or from title calls."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=2)):
        await client.post(_chat_url(), json={"content": "hi"})
        await client.post(_chat_url(), json={"content": "hi"})

        refused = await client.post(_generate_title_url())

    assert refused.status_code == 429


async def test_chat_is_rate_limited_per_ip(client: AsyncClient):
    """The ceiling applies on entry, so even an unauthenticated caller that
    never reaches the agent spends the IP's budget and is refused."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=3)):
        for _ in range(3):
            response = await client.post(_chat_url(), json={"content": "hi"})
            assert response.status_code == 401

        refused = await client.post(_chat_url(), json={"content": "hi"})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


async def test_chat_limit_is_per_client_ip(client: AsyncClient):
    """One visitor exhausting the ceiling must not lock out another: a bucket
    shared by every IP would fail the second request below with a 429."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=2)):
        for _ in range(2):
            response = await client.post(_chat_url(), json={"content": "hi"})
            assert response.status_code == 401
        assert (await client.post(_chat_url(), json={"content": "hi"})).status_code == 429

        other_visitor = ASGITransport(app=app, client=("198.51.100.7", 443))
        async with AsyncClient(transport=other_visitor, base_url="http://test") as other:
            from_elsewhere = await other.post(_chat_url(), json={"content": "hi"})

    assert from_elsewhere.status_code == 401


async def test_chat_limit_is_shared_by_every_agent(client: AsyncClient):
    """Cycling agent ids must not hand a caller a fresh budget per path."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=2)):
        first = await client.post(_chat_url(), json={"content": "hi"})
        second = await client.post(_chat_url(), json={"content": "hi"})
        refused = await client.post(_chat_url(), json={"content": "hi"})

    assert (first.status_code, second.status_code) == (401, 401)
    assert refused.status_code == 429


async def test_two_requests_in_the_same_instant_each_spend_a_slot(client: AsyncClient):
    """The window is a sorted set, so each request needs its own member: a
    member named after the timestamp would let two posts that share a float
    collapse into one, and a third would pass on a ceiling of two."""
    windows: dict[str, dict[str, float]] = {}

    def make_pipeline():
        pending: list[tuple] = []
        pipe = MagicMock()

        def zremrangebyscore(key, low, high):
            pending.append(("trim", key, low, high))
            return pipe

        def zcard(key):
            pending.append(("card", key))
            return pipe

        def zadd(key, mapping):
            pending.append(("add", key, dict(mapping)))
            return pipe

        def expire(key, seconds):
            pending.append(("expire", key, seconds))
            return pipe

        async def execute():
            results = []
            for op in pending:
                if op[0] == "trim":
                    _, key, low, high = op
                    window = windows.setdefault(key, {})
                    for member, score in list(window.items()):
                        if low <= score <= high:
                            del window[member]
                    results.append(None)
                elif op[0] == "card":
                    results.append(len(windows.setdefault(op[1], {})))
                elif op[0] == "add":
                    _, key, mapping = op
                    windows.setdefault(key, {}).update(mapping)
                    results.append(True)
                else:
                    results.append(True)
            return results

        pipe.zremrangebyscore = zremrangebyscore
        pipe.zcard = zcard
        pipe.zadd = zadd
        pipe.expire = expire
        pipe.execute = execute
        return pipe

    honest_redis = MagicMock()
    honest_redis.pipeline = make_pipeline

    async def _honest_get_redis():
        return honest_redis

    with patch("app.core.rate_limit.get_redis", _honest_get_redis), \
         patch("app.core.rate_limit.time.time", return_value=1_000.0), \
         patch("app.core.rate_limit.get_settings", lambda: _settings(limit=2)):
        first = await client.post(_chat_url(), json={"content": "hi"})
        second = await client.post(_chat_url(), json={"content": "hi"})
        refused = await client.post(_chat_url(), json={"content": "hi"})

    assert (first.status_code, second.status_code) == (401, 401)
    assert refused.status_code == 429


async def test_zero_disables_the_chat_limit(client: AsyncClient):
    """More requests than the shipped default, so a zero that quietly fell back
    to the default would be refused on the surplus instead of passing here."""
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=0)):
        responses = [
            await client.post(_chat_url(), json={"content": "hi"})
            for _ in range(_shipped_default() + 5)
        ]

    assert {r.status_code for r in responses} == {401}


@pytest.mark.parametrize("where_it_breaks", ["acquiring the connection", "pipeline"])
async def test_chat_refuses_with_503_when_the_limit_cannot_be_counted(
    client: AsyncClient, where_it_breaks: str
):
    """A Redis restart must not turn into a bare 500 the client cannot read,
    whether it fails when the connection is taken or in the middle of the
    pipeline. The ceiling is pinned so a developer's `0` cannot skip Redis."""

    def _broken_pipeline():
        pipe = MagicMock()
        for step in ("zremrangebyscore", "zcard", "zadd", "expire"):
            setattr(pipe, step, lambda *a, **kw: pipe)
        pipe.execute = AsyncMock(side_effect=RedisConnectionError("redis went away"))
        return pipe

    async def _unreachable():
        raise RedisConnectionError("redis is down")

    async def _connected_but_broken():
        redis = AsyncMock()
        redis.pipeline = _broken_pipeline
        return redis

    stub = _unreachable if where_it_breaks == "acquiring the connection" else _connected_but_broken

    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=_shipped_default())), \
         patch("app.core.rate_limit.get_redis", stub):
        response = await client.post(_chat_url(), json={"content": "hi"})

    assert response.status_code == 503


async def test_shared_limiters_keep_their_previous_error_handling():
    """The 503 is opt-in, set by the chat limiter alone: login, registration
    and password reset must keep answering exactly what they answered before
    this change, which is the step that keeps the diff inside #1591."""

    async def _unreachable():
        raise RedisConnectionError("redis is down")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": ("203.0.113.9", 1234),
        }
    )

    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=5)), \
         patch("app.core.rate_limit.get_redis", _unreachable):
        with pytest.raises(RedisConnectionError):
            await login_rate_limit(request)


async def test_default_chat_limit_leaves_room_for_a_normal_burst(client: AsyncClient):
    """A self-hosted instance must not feel the limiter in normal use."""
    default = _shipped_default()
    with patch("app.core.rate_limit.get_settings", lambda: _settings(limit=default)):
        responses = [
            await client.post(_chat_url(), json={"content": "hi"}) for _ in range(12)
        ]

    assert {r.status_code for r in responses} == {401}


def test_default_chat_limit_is_generous():
    assert _shipped_default() >= 20
