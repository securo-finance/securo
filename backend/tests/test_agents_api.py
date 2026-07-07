"""Agent + conversation CRUD via HTTP. Verifies auth, multi-tenant scoping,
and field handling. Does not exercise the LLM (see test_agents_executor.py).
"""
import uuid
from unittest.mock import patch

import bcrypt as _bcrypt
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


pytestmark = pytest.mark.asyncio


# Patch MCPRegistry.discover so /api/agents/{id}/tools doesn't try to hit a
# real MCP server during HTTP tests. Returns an empty tool list.
@pytest.fixture(autouse=True)
def _mock_mcp_discover():
    async def _empty_discover(self, *, user_id, workspace_id=None, conversation_id=None, agent_id=None):
        return []
    with patch("app.agents.api.agents.MCPRegistry.discover", new=_empty_discover):
        yield


@pytest_asyncio.fixture
async def other_user(session: AsyncSession) -> User:
    """A second user in the same DB to verify cross-tenant isolation."""
    from app.services.workspace_service import create_personal_workspace_for_user

    hashed = _bcrypt.hashpw(b"otherpass123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"language": "en", "currency_display": "USD"},
    )
    session.add(user)
    await session.flush()
    await create_personal_workspace_for_user(session, user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def other_auth_headers(client: AsyncClient, other_user: User) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": "other@example.com", "password": "otherpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- Info endpoints --------------------------------------------------------

async def test_app_info_reports_agents_enabled(client: AsyncClient):
    r = await client.get("/api/info")
    assert r.status_code == 200
    assert r.json()["features"]["agents"] is True


async def test_agents_info_lists_providers(client: AsyncClient, auth_headers: dict):
    r = await client.get("/api/agents/info", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert set(body["providers"]) >= {"openai", "anthropic", "ollama", "openai_compatible"}


async def test_agents_info_exposes_mcp_external_ttl(client: AsyncClient, auth_headers: dict):
    """The frontend reads mcp_external_ttl_days to label the token panel."""
    r = await client.get("/api/agents/info", headers=auth_headers)
    body = r.json()
    assert isinstance(body["mcp_external_ttl_days"], int)
    assert body["mcp_external_ttl_days"] >= 1


async def test_agents_info_exposes_external_mcp_url(client: AsyncClient, auth_headers: dict):
    """The frontend uses external_mcp_url (empty by default) so deployments
    behind an ingress can override the hardcoded :8765 endpoint."""
    r = await client.get("/api/agents/info", headers=auth_headers)
    body = r.json()
    assert "external_mcp_url" in body
    assert isinstance(body["external_mcp_url"], str)


async def test_agents_info_returns_configured_external_mcp_url(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """When AGENTS_EXTERNAL_MCP_URL is set, it is surfaced verbatim (trimmed)."""
    from app.agents import config

    config.get_agent_settings.cache_clear()
    monkeypatch.setenv("AGENTS_EXTERNAL_MCP_URL", "  https://securo.example.com/mcp  ")
    try:
        r = await client.get("/api/agents/info", headers=auth_headers)
        assert r.json()["external_mcp_url"] == "https://securo.example.com/mcp"
    finally:
        monkeypatch.delenv("AGENTS_EXTERNAL_MCP_URL", raising=False)
        config.get_agent_settings.cache_clear()


# --- External MCP tokens ---------------------------------------------------

async def test_mcp_tokens_requires_auth(client: AsyncClient):
    """The mint endpoint must reject unauthenticated calls — otherwise any
    anonymous visitor could mint a long-lived token for an arbitrary user."""
    r = await client.post("/api/agents/mcp-tokens")
    assert r.status_code == 401


async def test_mcp_tokens_mint_returns_external_jwt(
    client: AsyncClient, auth_headers: dict, test_user
):
    """End-to-end: minted token decodes, carries the `ext` claim, scoped
    to the calling user, and the advertised TTL matches the response."""
    from app.agents.mcp.auth import JWT_ALGO, JWT_AUDIENCE, JWT_ISSUER
    from app.agents.config import get_agent_settings
    from jose import jwt

    r = await client.post("/api/agents/mcp-tokens", headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "token" in body
    assert body["expires_in_days"] == get_agent_settings().mcp_external_ttl_days
    assert body["expires_in_seconds"] == body["expires_in_days"] * 86400

    payload = jwt.decode(
        body["token"],
        get_agent_settings().mcp_jwt_secret,
        algorithms=[JWT_ALGO],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )
    assert payload["sub"] == str(test_user.id)
    assert payload["ext"] is True
    # External tokens are detached from any conv/agent.
    assert "conv_id" not in payload
    assert "agent_id" not in payload


# --- Agent CRUD ------------------------------------------------------------

async def test_unauthenticated_agents_list_rejected(client: AsyncClient):
    r = await client.get("/api/agents")
    assert r.status_code == 401


async def test_create_list_get_update_delete(client: AsyncClient, auth_headers: dict):
    # Empty by default
    r = await client.get("/api/agents", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []

    # Create
    payload = {
        "name": "Brazilian Tax Advisor",
        "description": "Helps with PT-BR tax questions",
        "system_prompt": "You are a tax advisor.",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.3,
    }
    r = await client.post("/api/agents", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "Brazilian Tax Advisor"
    assert created["provider"] == "openai"
    assert created["temperature"] == 0.3

    aid = created["id"]

    # List shows the new agent
    r = await client.get("/api/agents", headers=auth_headers)
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == aid

    # Get by id
    r = await client.get(f"/api/agents/{aid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == aid

    # Update
    r = await client.patch(f"/api/agents/{aid}", json={"description": "New desc", "temperature": 0.7}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["description"] == "New desc"
    assert r.json()["temperature"] == 0.7

    # Delete
    r = await client.delete(f"/api/agents/{aid}", headers=auth_headers)
    assert r.status_code == 204

    # Gone
    r = await client.get(f"/api/agents/{aid}", headers=auth_headers)
    assert r.status_code == 404


async def test_multi_tenant_scoping(client: AsyncClient, auth_headers: dict, other_auth_headers: dict):
    """User A's agent is invisible to user B."""
    r = await client.post("/api/agents", json={"name": "Mine"}, headers=auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    # Other user lists their own agents — empty.
    r = await client.get("/api/agents", headers=other_auth_headers)
    assert r.status_code == 200
    assert r.json() == []

    # Other user can't read it directly.
    r = await client.get(f"/api/agents/{aid}", headers=other_auth_headers)
    assert r.status_code == 404

    # Or update.
    r = await client.patch(f"/api/agents/{aid}", json={"name": "hijack"}, headers=other_auth_headers)
    assert r.status_code == 404

    # Or delete.
    r = await client.delete(f"/api/agents/{aid}", headers=other_auth_headers)
    assert r.status_code == 404


async def test_create_validation_rejects_missing_name(client: AsyncClient, auth_headers: dict):
    r = await client.post("/api/agents", json={}, headers=auth_headers)
    assert r.status_code == 422


async def test_temperature_clamp(client: AsyncClient, auth_headers: dict):
    # Pydantic schema enforces 0..2
    r = await client.post("/api/agents", json={"name": "x", "temperature": 5.0}, headers=auth_headers)
    assert r.status_code == 422


# --- Conversations & messages ---------------------------------------------

async def test_conversations_empty(client: AsyncClient, auth_headers: dict):
    r = await client.get("/api/agents/conversations", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


async def test_conversation_filtered_by_agent(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    """Insert a conversation directly and verify the filter works."""
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    a1 = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A1")
    a2 = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A2")
    session.add_all([a1, a2])
    await session.commit()

    c1 = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=a1.id, channel="web", title="hello")
    c2 = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=a2.id, channel="web", title="other")
    session.add_all([c1, c2])
    await session.commit()

    r = await client.get(f"/api/agents/conversations?agent_id={a1.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "hello"


async def test_rename_conversation(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web", title="old")
    session.add_all([agent, conv])
    await session.commit()

    r = await client.patch(f"/api/agents/conversations/{conv.id}", json={"title": "renamed!"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "renamed!"

    r = await client.get(f"/api/agents/conversations/{conv.id}", headers=auth_headers)
    assert r.json()["title"] == "renamed!"


async def test_rename_conversation_validates_empty(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web", title="old")
    session.add_all([agent, conv])
    await session.commit()

    r = await client.patch(f"/api/agents/conversations/{conv.id}", json={"title": ""}, headers=auth_headers)
    assert r.status_code == 422


async def test_rename_conversation_404_other_user(
    client: AsyncClient, auth_headers: dict, other_auth_headers: dict, session: AsyncSession, test_user: User
):
    """auth_headers must come BEFORE other_auth_headers so test_user's
    clean_db setup runs first — otherwise it wipes the other user that
    other_auth_headers already created."""
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web", title="old")
    session.add_all([agent, conv])
    await session.commit()

    r = await client.patch(
        f"/api/agents/conversations/{conv.id}", json={"title": "hijack"}, headers=other_auth_headers
    )
    assert r.status_code == 404


async def test_delete_conversation(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web")
    session.add_all([agent, conv])
    await session.commit()

    r = await client.delete(f"/api/agents/conversations/{conv.id}", headers=auth_headers)
    assert r.status_code == 204

    r = await client.get(f"/api/agents/conversations/{conv.id}", headers=auth_headers)
    assert r.status_code == 404


async def test_generate_title_uses_llm(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    """The endpoint asks the agent's LLM provider for a short summary
    and persists it. We patch _provider_and_model_for so no real LLM
    call is made."""
    from unittest.mock import patch
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation, Message
    from app.agents.providers.base import (
        ChatResponse,
        LLMProvider,
        Usage,
    )

    class _Scripted(LLMProvider):
        name = "openai"

        async def chat_stream(self, messages, *, model, tools=None, temperature=0.4, max_tokens=None):
            # not used for title (we call .chat() which goes through .chat_stream)
            yield  # pragma: no cover
            return  # pragma: no cover

        async def chat(self, messages, *, model, tools=None, temperature=0.4, max_tokens=None):
            return ChatResponse(content="Brazilian Cuisine Picks", usage=Usage(2, 4))

        async def embed(self, texts, *, model):
            return []

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A", provider="openai", model="gpt-4o-mini")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web", title="name three brazilian foods")
    session.add_all([agent, conv])
    await session.commit()

    session.add_all([
        Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=1, role="user", content="name three brazilian foods"),
        Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=2, role="assistant", content="Feijoada, Coxinha, Pão de Queijo"),
    ])
    await session.commit()

    with patch(
        "app.agents.runtime.executor._provider_and_model_for",
        return_value=(_Scripted(), "gpt-4o-mini"),
    ):
        r = await client.post(
            f"/api/agents/conversations/{conv.id}/generate-title",
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Brazilian Cuisine Picks"


async def test_generate_title_strips_reasoning_tags(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    """Local reasoning models often emit `<think>...</think>` before the
    answer. The endpoint must strip that and use only the title."""
    from unittest.mock import patch
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation, Message
    from app.agents.providers.base import ChatResponse, LLMProvider, Usage

    class _Scripted(LLMProvider):
        name = "openai_compatible"

        async def chat_stream(self, *a, **kw):
            yield  # pragma: no cover

        async def chat(self, *a, **kw):
            return ChatResponse(
                content="<think>Let me summarize…</think>\nQuick Math Question",
                usage=Usage(0, 0),
            )

        async def embed(self, *a, **kw):
            return []

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A", model="local")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web", title="2+2")
    session.add_all([agent, conv])
    await session.commit()
    session.add_all([
        Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=1, role="user", content="2+2"),
        Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=2, role="assistant", content="4"),
    ])
    await session.commit()

    with patch(
        "app.agents.runtime.executor._provider_and_model_for",
        return_value=(_Scripted(), "local"),
    ):
        r = await client.post(
            f"/api/agents/conversations/{conv.id}/generate-title",
            headers=auth_headers,
        )
    assert r.status_code == 200
    # Just the title — the <think> block is gone.
    assert r.json()["title"] == "Quick Math Question"


# --- Tools endpoint --------------------------------------------------------

async def test_tools_endpoint_returns_empty_when_no_mcp(client: AsyncClient, auth_headers: dict):
    r = await client.post("/api/agents", json={"name": "T"}, headers=auth_headers)
    aid = r.json()["id"]

    r = await client.get(f"/api/agents/{aid}/tools", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["tools"] == []


async def test_put_tools_persists_selection(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    """Even without a live MCP server, the user can pre-stage a whitelist."""
    from app.agents.models.agent import Agent

    a = Agent(id=uuid.uuid4(), user_id=test_user.id, name="X")
    session.add(a)
    await session.commit()

    r = await client.put(
        f"/api/agents/{a.id}/tools",
        json=[
            {"server": "securo", "tool_name": "list_transactions", "enabled": True},
            {"server": "securo", "tool_name": "propose_categorize", "enabled": False},
        ],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2

    # Verify rows actually exist via the service.
    from app.agents.services import agent_service

    pairs = await agent_service.allowed_tool_pairs(session, a.id)
    assert pairs == {("securo", "list_transactions")}
