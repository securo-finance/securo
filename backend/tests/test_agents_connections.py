"""LLM connections — encryption, CRUD via HTTP, executor wiring."""
import uuid
from unittest.mock import patch, AsyncMock

import bcrypt as _bcrypt
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services import connection_service
from app.agents.services.crypto import decrypt, encrypt
from app.models.user import User


pytestmark = pytest.mark.asyncio


# --- Encryption ------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    secret = "sk-ant-totally-secret-12345"
    enc = encrypt(secret)
    assert enc != secret
    assert enc and len(enc) > 40  # Fernet tokens are noticeably longer than the input
    assert decrypt(enc) == secret


def test_encrypt_none_passes_through():
    assert encrypt(None) is None
    assert encrypt("") is None
    assert decrypt(None) is None
    assert decrypt("") is None


def test_decrypt_corrupt_returns_none():
    """Rotated secret / corrupt ciphertext must not crash; returns None."""
    assert decrypt("not-a-real-fernet-token") is None


# --- Service layer --------------------------------------------------------

async def test_create_connection_with_encryption(session: AsyncSession, test_user: User):
    conn = await connection_service.create_connection(
        session, test_user.id,
        name="Local Ollama",
        kind="ollama",
        base_url="http://host.docker.internal:11434",
        api_key=None,
        default_model="llama3.1:8b",
    )
    assert conn.id is not None
    assert conn.api_key_encrypted is None  # ollama doesn't need a key

    conn2 = await connection_service.create_connection(
        session, test_user.id,
        name="My Anthropic",
        kind="anthropic",
        api_key="sk-ant-real-key",
        default_model="claude-haiku-4-5",
    )
    assert conn2.api_key_encrypted is not None
    assert conn2.api_key_encrypted != "sk-ant-real-key"
    assert decrypt(conn2.api_key_encrypted) == "sk-ant-real-key"


async def test_create_connection_rejects_unknown_kind(session: AsyncSession, test_user: User):
    with pytest.raises(ValueError):
        await connection_service.create_connection(
            session, test_user.id, name="x", kind="bogus",
        )


async def test_openai_compatible_requires_base_url(session: AsyncSession, test_user: User):
    with pytest.raises(ValueError):
        await connection_service.create_connection(
            session, test_user.id, name="x", kind="openai_compatible",
        )


async def test_only_one_default_per_user(session: AsyncSession, test_user: User):
    a = await connection_service.create_connection(
        session, test_user.id, name="A", kind="ollama", is_default=True,
    )
    b = await connection_service.create_connection(
        session, test_user.id, name="B", kind="ollama", is_default=True,
    )
    # Re-fetch a — should no longer be default.
    a_after = await connection_service.get_connection(session, a.id, test_user.id)

    assert a_after is not None
    assert a_after.is_default is False
    assert b.is_default is True


async def test_update_keeps_existing_key_when_api_key_omitted(session: AsyncSession, test_user: User):
    conn = await connection_service.create_connection(
        session, test_user.id, name="x", kind="openai", api_key="sk-original",
    )
    enc_before = conn.api_key_encrypted

    # Update without touching api_key.
    updated = await connection_service.update_connection(
        session, conn.id, test_user.id, name="renamed",
    )

    assert updated is not None
    assert updated.api_key_encrypted == enc_before
    assert decrypt(updated.api_key_encrypted) == "sk-original"


async def test_update_clears_key_with_empty_string(session: AsyncSession, test_user: User):
    conn = await connection_service.create_connection(
        session, test_user.id, name="x", kind="openai", api_key="sk-original",
    )
    updated = await connection_service.update_connection(
        session, conn.id, test_user.id, api_key="",
    )

    assert updated is not None
    assert updated.api_key_encrypted is None


# --- HTTP CRUD -------------------------------------------------------------

async def test_unauthenticated_connections_list_rejected(client: AsyncClient):
    r = await client.get("/api/agents/connections")
    assert r.status_code == 401


async def test_create_then_list_via_http(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        "/api/agents/connections",
        json={
            "name": "Local Ollama",
            "kind": "ollama",
            "base_url": "http://host.docker.internal:11434",
            "default_model": "llama3.1:8b",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Local Ollama"
    assert body["has_api_key"] is False
    assert "api_key" not in body  # never expose key

    r = await client.get("/api/agents/connections", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_api_key_never_returned(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "secret", "kind": "openai", "api_key": "sk-real-and-private"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert "api_key" not in r.json()
    assert r.json()["has_api_key"] is True

    r = await client.get(f"/api/agents/connections/{cid}", headers=auth_headers)
    body = r.json()
    assert "api_key" not in body
    # The plaintext should not appear anywhere in the response.
    assert "sk-real-and-private" not in r.text


@pytest_asyncio.fixture
async def other_auth_headers_conn(client: AsyncClient, session: AsyncSession) -> dict:
    """Second user just for tenant-isolation tests in this file."""
    hashed = _bcrypt.hashpw(b"otherpass123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email="other-conn@example.com",
        hashed_password=hashed,
        is_active=True, is_superuser=False, is_verified=True,
        preferences={"language": "en", "currency_display": "USD"},
    )
    session.add(user)
    await session.commit()
    resp = await client.post(
        "/api/auth/login",
        data={"username": "other-conn@example.com", "password": "otherpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_connections_are_per_user(client: AsyncClient, auth_headers: dict, other_auth_headers_conn: dict):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "mine", "kind": "ollama"},
        headers=auth_headers,
    )
    cid = r.json()["id"]

    r = await client.get("/api/agents/connections", headers=other_auth_headers_conn)
    assert r.json() == []

    r = await client.get(f"/api/agents/connections/{cid}", headers=other_auth_headers_conn)
    assert r.status_code == 404


# --- Test endpoint (mocked HTTP probe) ------------------------------------

async def test_test_endpoint_mocked_ok(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        "/api/agents/connections",
        json={"name": "x", "kind": "ollama", "base_url": "http://fake:11434"},
        headers=auth_headers,
    )
    cid = r.json()["id"]

    fake_resp = AsyncMock()
    fake_resp.raise_for_status = lambda: None
    fake_resp.json = lambda: {"models": [{"name": "llama3.1:8b"}]}

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.get = AsyncMock(return_value=fake_resp)

    # `httpx` is imported lazily inside test_connection(); patch the global.
    with patch("httpx.AsyncClient", return_value=fake_client):
        r = await client.post(f"/api/agents/connections/{cid}/test", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["models"] == ["llama3.1:8b"]


async def test_test_endpoint_404_for_unknown(client: AsyncClient, auth_headers: dict):
    r = await client.post(f"/api/agents/connections/{uuid.uuid4()}/test", headers=auth_headers)
    assert r.status_code == 404


# --- Executor uses the connection ------------------------------------------

async def test_executor_resolves_provider_from_connection(
    session: AsyncSession, test_user: User
):
    """Agent with connection_id → executor builds provider via the connection,
    inheriting its base_url and decrypted api_key. The connection's
    default_model is used when the agent doesn't override."""
    from app.agents.runtime.executor import _provider_and_model_for
    from app.agents.models.agent import Agent

    conn = await connection_service.create_connection(
        session, test_user.id,
        name="my-llm", kind="openai_compatible",
        base_url="http://my-server:8000/v1",
        api_key="sk-some-key",
        default_model="my-custom-model",
    )

    agent = Agent(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="A",
        connection_id=conn.id,
        model=None,  # falls back to connection.default_model
    )
    session.add(agent)
    await session.commit()

    provider, model = await _provider_and_model_for(session, agent)
    assert provider.name == "openai_compatible"
    assert provider.base_url == "http://my-server:8000/v1"
    assert provider.api_key == "sk-some-key"
    assert model == "my-custom-model"


async def test_executor_falls_back_to_user_default_connection(
    session: AsyncSession, test_user: User
):
    """No connection_id on agent, but user has a default connection — use it."""
    from app.agents.runtime.executor import _provider_and_model_for
    from app.agents.models.agent import Agent

    await connection_service.create_connection(
        session, test_user.id,
        name="default-anthropic", kind="anthropic",
        api_key="sk-ant-x", default_model="claude-haiku-4-5",
        is_default=True,
    )

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A", connection_id=None, model=None)
    session.add(agent)
    await session.commit()

    provider, model = await _provider_and_model_for(session, agent)
    assert provider.name == "anthropic"
    assert provider.api_key == "sk-ant-x"
    assert model == "claude-haiku-4-5"


async def test_executor_agent_model_overrides_connection_default(
    session: AsyncSession, test_user: User
):
    from app.agents.runtime.executor import _provider_and_model_for
    from app.agents.models.agent import Agent

    conn = await connection_service.create_connection(
        session, test_user.id,
        name="x", kind="openai", api_key="sk", default_model="gpt-4o-mini",
    )
    agent = Agent(
        id=uuid.uuid4(), user_id=test_user.id, name="A",
        connection_id=conn.id, model="gpt-4o",  # explicit override
    )
    session.add(agent)
    await session.commit()

    _provider, model = await _provider_and_model_for(session, agent)
    assert model == "gpt-4o"
