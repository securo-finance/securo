"""Coverage for app/agents/services/connection_service.

The CRUD paths use the real test SQLite session. test_connection() is
the largest function in the module — we drive it through a fake
httpx.AsyncClient so each provider branch is exercised without real
network calls.
"""
from __future__ import annotations

import uuid

import pytest

import app.agents.services.connection_service as cs


# --------------------------------------------------------------------- CRUD

@pytest.mark.asyncio
async def test_create_connection_rejects_unknown_kind(session, test_user):
    with pytest.raises(ValueError, match="unknown kind"):
        await cs.create_connection(session, test_user.id, name="x", kind="bogus")


@pytest.mark.asyncio
async def test_create_connection_requires_base_url_for_openai_compatible(session, test_user):
    with pytest.raises(ValueError, match="openai_compatible requires base_url"):
        await cs.create_connection(session, test_user.id, name="x", kind="openai_compatible")


@pytest.mark.asyncio
async def test_create_and_get_and_list(session, test_user):
    conn = await cs.create_connection(
        session, test_user.id,
        name="My OpenAI", kind="openai", api_key="sk-secret",
        default_model="gpt-4o-mini",
    )
    assert conn.id is not None
    # API key is stored encrypted, NOT plaintext.
    assert conn.api_key_encrypted != b"sk-secret"
    assert conn.api_key_encrypted

    fetched = await cs.get_connection(session, conn.id, test_user.id)
    assert fetched is not None and fetched.id == conn.id

    # Wrong user → invisible.
    other = uuid.uuid4()
    assert await cs.get_connection(session, conn.id, other) is None

    listed = await cs.list_connections(session, test_user.id)
    assert any(c.id == conn.id for c in listed)


@pytest.mark.asyncio
async def test_default_is_unique_per_user(session, test_user):
    a = await cs.create_connection(
        session, test_user.id, name="a", kind="openai", is_default=True,
    )
    b = await cs.create_connection(
        session, test_user.id, name="b", kind="openai", is_default=True,
    )
    # Re-fetch a — its default flag should be cleared.
    refreshed_a = await cs.get_connection(session, a.id, test_user.id)

    assert refreshed_a is not None
    await session.refresh(refreshed_a)
    assert refreshed_a.is_default is False
    refreshed_b = await cs.get_connection(session, b.id, test_user.id)

    assert refreshed_b is not None
    await session.refresh(refreshed_b)
    assert refreshed_b.is_default is True

    # get_default_connection returns b
    default = await cs.get_default_connection(session, test_user.id)
    assert default is not None and default.id == b.id


@pytest.mark.asyncio
async def test_update_connection_handles_partial_and_default_swap(session, test_user):
    a = await cs.create_connection(session, test_user.id, name="a", kind="openai", is_default=True)
    b = await cs.create_connection(session, test_user.id, name="b", kind="openai")

    updated = await cs.update_connection(
        session, b.id, test_user.id,
        name="renamed", api_key="new-key", default_model="gpt-4o",
        extra={"note": "yo"}, is_default=True,
    )
    assert updated is not None
    assert updated.name == "renamed"
    assert updated.default_model == "gpt-4o"
    assert updated.extra == {"note": "yo"}
    assert updated.is_default is True

    # a should be demoted.
    refreshed_a = await cs.get_connection(session, a.id, test_user.id)

    assert refreshed_a is not None
    await session.refresh(refreshed_a)
    assert refreshed_a.is_default is False

    # Setting is_default=False explicitly clears it.
    cleared = await cs.update_connection(session, b.id, test_user.id, is_default=False)
    assert cleared is not None and cleared.is_default is False


@pytest.mark.asyncio
async def test_update_connection_returns_none_for_unknown(session, test_user):
    out = await cs.update_connection(session, uuid.uuid4(), test_user.id, name="x")
    assert out is None


@pytest.mark.asyncio
async def test_update_connection_can_clear_api_key(session, test_user):
    """Passing api_key='' should null out the stored encrypted key."""
    conn = await cs.create_connection(
        session, test_user.id, name="x", kind="openai", api_key="sk-old",
    )

    assert conn.api_key_encrypted is not None

    out = await cs.update_connection(session, conn.id, test_user.id, api_key="")
    assert out is not None
    assert out.api_key_encrypted is None


@pytest.mark.asyncio
async def test_delete_connection(session, test_user):
    conn = await cs.create_connection(session, test_user.id, name="x", kind="openai")
    assert await cs.delete_connection(session, conn.id, test_user.id) is True
    assert await cs.get_connection(session, conn.id, test_user.id) is None
    assert await cs.delete_connection(session, uuid.uuid4(), test_user.id) is False


def test_build_provider_for_connection(session):
    """The connection should yield a provider instance whose attrs come
    from the row. Smoke-test via openai kind."""
    from app.agents.models.connection import LlmConnection

    conn = LlmConnection(
        id=uuid.uuid4(), user_id=uuid.uuid4(),
        name="x", kind="openai",
        base_url="https://api.openai.com/v1",
        api_key_encrypted=None,
        default_model="gpt-4o-mini",
        extra={},
    )
    provider = cs.build_provider_for_connection(conn)
    assert provider.name == "openai"


# --------------------------------------------------------------------- test_connection (probe)

class _FakeResp:
    def __init__(self, *, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class _FakeAsyncClient:
    queue: list[_FakeResp] = []
    calls: list[tuple[str, dict]] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, headers=None):
        type(self).calls.append((url, headers or {}))
        return type(self).queue.pop(0)


@pytest.fixture
def fake_httpx(monkeypatch):
    _FakeAsyncClient.queue = []
    _FakeAsyncClient.calls = []
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    yield _FakeAsyncClient


def _conn(**overrides):
    from app.agents.models.connection import LlmConnection
    defaults = dict(
        id=uuid.uuid4(), user_id=uuid.uuid4(),
        name="x", kind="openai",
        base_url=None, api_key_encrypted=None,
        default_model=None, extra={},
    )
    defaults.update(overrides)
    return LlmConnection(**defaults)


@pytest.mark.asyncio
async def test_probe_ollama_returns_model_count(fake_httpx):
    fake_httpx.queue.append(_FakeResp(json_body={
        "models": [{"name": "llama3"}, {"name": "nomic"}],
    }))
    out = await cs.test_connection(_conn(kind="ollama"))
    assert out["ok"] is True
    assert "2 models" in out["detail"]
    assert out["models"] == ["llama3", "nomic"]


@pytest.mark.asyncio
async def test_probe_openai_returns_models_on_success(fake_httpx):
    fake_httpx.queue.append(_FakeResp(json_body={
        "data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}],
    }))
    out = await cs.test_connection(_conn(kind="openai"))
    assert out["ok"] is True
    assert "2 models" in out["detail"]


@pytest.mark.asyncio
async def test_probe_openai_auth_rejected(fake_httpx):
    fake_httpx.queue.append(_FakeResp(status_code=401))
    out = await cs.test_connection(_conn(kind="openai"))
    assert out["ok"] is False
    assert "auth rejected" in out["detail"]


@pytest.mark.asyncio
async def test_probe_openai_returns_http_error_text(fake_httpx):
    fake_httpx.queue.append(_FakeResp(status_code=500, text="oops"))
    out = await cs.test_connection(_conn(kind="openai"))
    assert out["ok"] is False
    assert "HTTP 500" in out["detail"]


@pytest.mark.asyncio
async def test_probe_openai_compatible_warns_when_no_models(fake_httpx):
    fake_httpx.queue.append(_FakeResp(json_body={"data": []}))
    out = await cs.test_connection(_conn(
        kind="openai_compatible", base_url="http://lmstudio:1234/v1",
    ))
    assert out["ok"] is False
    assert "no models" in out["detail"]


@pytest.mark.asyncio
async def test_probe_openai_compatible_warns_when_bad_json(fake_httpx):
    fake_httpx.queue.append(_FakeResp(json_body=ValueError("not json")))
    out = await cs.test_connection(_conn(
        kind="openai_compatible", base_url="http://lmstudio:1234/v1",
    ))
    assert out["ok"] is False
    assert "did not return JSON" in out["detail"]


@pytest.mark.asyncio
async def test_probe_anthropic_success(fake_httpx):
    fake_httpx.queue.append(_FakeResp(json_body={
        "data": [{"id": "claude-3-5-sonnet"}],
    }))
    out = await cs.test_connection(_conn(kind="anthropic"))
    assert out["ok"] is True
    assert "1 models" in out["detail"]


@pytest.mark.asyncio
async def test_probe_anthropic_auth_failure(fake_httpx):
    fake_httpx.queue.append(_FakeResp(status_code=403))
    out = await cs.test_connection(_conn(kind="anthropic"))
    assert out["ok"] is False
    assert "auth rejected" in out["detail"]


@pytest.mark.asyncio
async def test_probe_unknown_kind(fake_httpx):
    out = await cs.test_connection(_conn(kind="something-else"))
    assert out["ok"] is False
    assert "unknown kind" in out["detail"]


@pytest.mark.asyncio
async def test_probe_swallows_httpx_errors(monkeypatch):
    """If the AsyncClient itself raises (network down), we should return
    a structured 'unreachable' response, not propagate."""
    import httpx

    class _BlowsUp:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("dns fail")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _BlowsUp)
    out = await cs.test_connection(_conn(kind="ollama"))
    assert out["ok"] is False
    assert "unreachable" in out["detail"]


@pytest.mark.asyncio
async def test_probe_names_the_error_when_it_stringifies_empty(monkeypatch):
    """httpx connect timeouts carry no message, which used to render as a
    bare 'unreachable:' with no reason and no address in the UI."""
    import httpx

    class _TimesOut:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            raise httpx.ConnectTimeout("")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _TimesOut)
    out = await cs.test_connection(
        _conn(kind="openai_compatible", base_url="http://192.168.1.152:1234")
    )
    assert out["ok"] is False
    assert "ConnectTimeout" in out["detail"]
    assert "192.168.1.152:1234" in out["detail"]


def test_routing_hint_only_fires_inside_a_container(monkeypatch):
    """A LAN or loopback address the browser can reach is often unroutable
    from the container, so suggest the host gateway. Bare-metal installs
    reach those addresses fine and must not get the hint."""
    monkeypatch.setattr(cs, "_in_container", lambda: True)
    assert "host.docker.internal:1234" in cs._routing_hint("http://192.168.1.152:1234")
    assert "host.docker.internal:11434" in cs._routing_hint("http://localhost:11434")
    # Public hosts are reachable from anywhere; nothing to suggest.
    assert cs._routing_hint("https://api.openai.com/v1") == ""

    monkeypatch.setattr(cs, "_in_container", lambda: False)
    assert cs._routing_hint("http://192.168.1.152:1234") == ""
