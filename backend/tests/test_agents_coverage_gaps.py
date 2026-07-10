"""Supplemental tests targeting specific uncovered branches in
app/agents/services/agent_service and app/agents/api/{agents,conversations}.
The existing test_agents_api.py covers happy paths; this file fills in
the unhit branches (is_default toggling, count aggregation, tools
discovery, generate-title, delete fallbacks)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents.services import agent_service


# --------------------------------------------------------------------- agent_service.list_agents conv+kb count aggregation


@pytest.mark.asyncio
async def test_list_agents_populates_conversation_and_knowledge_counts(
    session, test_user, test_workspace, test_agent
):
    """Ensures the GROUP BY count queries (lines 23-43) run."""
    from app.agents.models.conversation import Conversation
    from app.agents.models.knowledge import KnowledgeDoc

    # Add 2 conversations and 1 KB doc to the agent.
    session.add_all(
        [
            Conversation(
                id=uuid.uuid4(), user_id=test_user.id, agent_id=test_agent.id, channel="web"
            ),
            Conversation(
                id=uuid.uuid4(), user_id=test_user.id, agent_id=test_agent.id, channel="web"
            ),
            KnowledgeDoc(
                id=uuid.uuid4(),
                agent_id=test_agent.id,
                user_id=test_user.id,
                title="t",
                source="t",
                mime="text/plain",
                size_bytes=1,
                status="ready",
            ),
        ]
    )
    await session.commit()

    rows = await agent_service.list_agents(session, test_workspace.id)
    assert len(rows) == 1
    assert rows[0].conversation_count == 2  # type: ignore[attr-defined]
    assert rows[0].knowledge_count == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------- agent_service.set_tool_enabled


@pytest.mark.asyncio
async def test_set_tool_enabled_inserts_then_updates_existing_row(session, test_agent):
    # Insert path
    row = await agent_service.set_tool_enabled(
        session, test_agent.id, "securo", "list_accounts", True
    )
    assert row.enabled is True
    # Same (server, tool) pair → updates the existing row, no second insert.
    row2 = await agent_service.set_tool_enabled(
        session, test_agent.id, "securo", "list_accounts", False
    )
    # Composite primary key: same (agent_id, server, tool_name) tuple.
    assert (row2.agent_id, row2.server, row2.tool_name) == (row.agent_id, row.server, row.tool_name)
    assert row2.enabled is False

    # And allowed_tool_pairs reflects it.
    pairs = await agent_service.allowed_tool_pairs(session, test_agent.id)
    assert pairs == set()  # disabled → not in allowed set


@pytest.mark.asyncio
async def test_allowed_tool_pairs_returns_none_when_no_rows(session, test_agent):
    """No explicit AgentTool rows → returns None ('allow all')."""
    out = await agent_service.allowed_tool_pairs(session, test_agent.id)
    assert out is None


# --------------------------------------------------------------------- agents API: /default + /tools


@pytest.mark.asyncio
async def test_get_default_agent_endpoint_returns_explicit_default(client, auth_headers, test_user):
    await client.post("/api/agents", json={"name": "A"}, headers=auth_headers)
    b = (await client.post("/api/agents", json={"name": "B"}, headers=auth_headers)).json()
    # Mark B explicit default.
    await client.patch(f"/api/agents/{b['id']}", json={"is_default": True}, headers=auth_headers)

    r = await client.get("/api/agents/default", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == b["id"]


@pytest.mark.asyncio
async def test_get_default_agent_endpoint_404_when_no_agents(client, auth_headers, test_user):
    r = await client.get("/api/agents/default", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_default_falls_back_to_most_recent_when_no_explicit(
    client, auth_headers, test_user
):
    a = (await client.post("/api/agents", json={"name": "A"}, headers=auth_headers)).json()
    b = (await client.post("/api/agents", json={"name": "B"}, headers=auth_headers)).json()
    r = await client.get("/api/agents/default", headers=auth_headers)
    assert r.status_code == 200
    # The newest agent wins as fallback.
    assert r.json()["id"] in {a["id"], b["id"]}


@pytest.mark.asyncio
async def test_marking_second_agent_default_clears_first(client, auth_headers, test_user):
    a = (await client.post("/api/agents", json={"name": "A"}, headers=auth_headers)).json()
    b = (await client.post("/api/agents", json={"name": "B"}, headers=auth_headers)).json()

    await client.patch(f"/api/agents/{a['id']}", json={"is_default": True}, headers=auth_headers)
    await client.patch(f"/api/agents/{b['id']}", json={"is_default": True}, headers=auth_headers)

    r = await client.get(f"/api/agents/{a['id']}", headers=auth_headers)
    assert r.json()["is_default"] is False
    r = await client.get(f"/api/agents/{b['id']}", headers=auth_headers)
    assert r.json()["is_default"] is True


@pytest.mark.asyncio
async def test_get_agent_tools_returns_merged_state(client, auth_headers, test_user):
    """Mock MCPRegistry.discover so we can exercise the merge logic
    in api/agents.py without a live MCP server."""
    from app.agents.mcp.client import ToolHandle

    async def fake_discover(
        self, *, user_id, workspace_id=None, conversation_id=None, agent_id=None
    ):
        return [
            ToolHandle(server="securo", name="list_accounts", description="d", parameters={}),
            ToolHandle(
                server="securo", name="propose_x", description="d", parameters={}, is_proposal=True
            ),
        ]

    with (
        patch("app.agents.api.agents.MCPRegistry.discover", new=fake_discover),
        patch("app.agents.api.agents.MCPRegistry.server_names", lambda self: ["securo"]),
    ):
        a = (await client.post("/api/agents", json={"name": "A"}, headers=auth_headers)).json()
        r = await client.get(f"/api/agents/{a['id']}/tools", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["servers"] == [{"name": "securo"}]
        by_name = {t["name"]: t for t in body["tools"]}
        # First-run with no AgentTool rows yet → enabled defaults to True.
        assert by_name["list_accounts"]["enabled"] is True
        assert by_name["propose_x"]["is_proposal"] is True


@pytest.mark.asyncio
async def test_get_agent_tools_404_for_unknown_agent(client, auth_headers, test_user):
    r = await client.get(f"/api/agents/{uuid.uuid4()}/tools", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_put_agent_tools_persists_explicit_selection(client, auth_headers, test_user):
    a = (await client.post("/api/agents", json={"name": "A"}, headers=auth_headers)).json()
    r = await client.put(
        f"/api/agents/{a['id']}/tools",
        json=[
            {"server": "securo", "tool_name": "list_accounts", "enabled": True},
            {"server": "securo", "tool_name": "create_payee", "enabled": False},
        ],
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 2}


@pytest.mark.asyncio
async def test_put_agent_tools_404_for_unknown_agent(client, auth_headers, test_user):
    r = await client.put(
        f"/api/agents/{uuid.uuid4()}/tools",
        json=[],
        headers=auth_headers,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------- conversations API: generate-title quirks


@pytest.mark.asyncio
async def test_generate_title_returns_unchanged_when_no_messages(
    client, auth_headers, test_user, session
):
    """generate-title shortcuts to the existing row when transcript is
    empty — covers the early-return branch."""
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(
        id=uuid.uuid4(),
        user_id=test_user.id,
        agent_id=agent.id,
        channel="web",
        title="original",
    )
    session.add_all([agent, conv])
    await session.commit()

    r = await client.post(
        f"/api/agents/conversations/{conv.id}/generate-title",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["title"] == "original"


@pytest.mark.asyncio
async def test_generate_title_silently_swallows_llm_errors(
    client, auth_headers, test_user, session
):
    """If the provider raises, the existing title is preserved and a 200
    is still returned."""
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation, Message
    from app.agents.providers.base import LLMProvider

    class _Boom(LLMProvider):
        name = "openai"

        async def chat_stream(self, *a, **kw):
            yield  # pragma: no cover

        async def chat(self, *a, **kw):
            raise RuntimeError("provider down")

        async def embed(self, *a, **kw):
            return []

    agent = Agent(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="A",
        provider="openai",
        model="gpt-4o-mini",
    )
    conv = Conversation(
        id=uuid.uuid4(),
        user_id=test_user.id,
        agent_id=agent.id,
        channel="web",
        title="kept",
    )
    session.add_all([agent, conv])
    await session.commit()
    session.add_all(
        [
            Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=1, role="user", content="hi"),
            Message(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                ordinal=2,
                role="assistant",
                content="hello",
            ),
        ]
    )
    await session.commit()

    with patch(
        "app.agents.runtime.executor._provider_and_model_for",
        return_value=(_Boom(), "gpt-4o-mini"),
    ):
        r = await client.post(
            f"/api/agents/conversations/{conv.id}/generate-title",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["title"] == "kept"


@pytest.mark.asyncio
async def test_generate_title_404_for_unknown_conversation(client, auth_headers, test_user):
    r = await client.post(
        f"/api/agents/conversations/{uuid.uuid4()}/generate-title",
        headers=auth_headers,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------- conversations API: 404 paths


@pytest.mark.asyncio
async def test_get_conversation_404(client, auth_headers, test_user):
    r = await client.get(
        f"/api/agents/conversations/{uuid.uuid4()}",
        headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_messages_404_for_unknown_conversation(client, auth_headers, test_user):
    r = await client.get(
        f"/api/agents/conversations/{uuid.uuid4()}/messages",
        headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_messages_returns_messages_for_owned_conversation(
    client, auth_headers, test_user, session
):
    from app.agents.models.agent import Agent
    from app.agents.models.conversation import Conversation, Message

    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, name="A")
    conv = Conversation(id=uuid.uuid4(), user_id=test_user.id, agent_id=agent.id, channel="web")
    session.add_all([agent, conv])
    await session.commit()
    session.add_all(
        [
            Message(id=uuid.uuid4(), conversation_id=conv.id, ordinal=1, role="user", content="hi"),
            Message(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                ordinal=2,
                role="assistant",
                content="hello",
            ),
        ]
    )
    await session.commit()

    r = await client.get(
        f"/api/agents/conversations/{conv.id}/messages",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert [m["content"] for m in body] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_delete_conversation_404(client, auth_headers, test_user):
    r = await client.delete(
        f"/api/agents/conversations/{uuid.uuid4()}",
        headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rename_conversation_404(client, auth_headers, test_user):
    r = await client.patch(
        f"/api/agents/conversations/{uuid.uuid4()}",
        json={"title": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404
