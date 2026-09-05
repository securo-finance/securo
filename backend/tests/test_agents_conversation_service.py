"""Direct unit tests for app/agents/services/conversation_service. The
HTTP layer covers most paths in test_agents_api.py — this fills in the
service-only utilities (append_message ordinal generation,
update_title_if_empty, etc.) that don't ride a route."""
from __future__ import annotations

import uuid

import pytest

from app.agents.services import conversation_service as svc


@pytest.mark.asyncio
async def test_create_conversation_persists_with_defaults(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    assert conv.id is not None
    assert conv.user_id == test_user.id
    assert conv.agent_id == test_agent.id
    assert conv.channel == "web"  # default
    assert conv.title is None


@pytest.mark.asyncio
async def test_list_conversations_filters_by_agent_and_user(session, test_user, test_workspace, test_agent):
    a = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id, title="A",
    )
    # Conversation for a different agent → should be filtered out.
    other_agent_id = uuid.uuid4()
    from app.agents.models.agent import Agent
    other_agent = Agent(id=other_agent_id, user_id=test_user.id, name="Other")
    session.add(other_agent)
    await session.commit()
    b = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=other_agent_id, title="B",
    )

    rows_a = await svc.list_conversations(session, test_workspace.id, agent_id=test_agent.id)
    assert {c.id for c in rows_a} == {a.id}

    # All of test_user's convs.
    rows_all = await svc.list_conversations(session, test_workspace.id)
    assert {a.id, b.id} <= {c.id for c in rows_all}

    # Foreign user → empty.
    foreign = uuid.uuid4()
    assert await svc.list_conversations(session, foreign) == []  # foreign workspace_id


@pytest.mark.asyncio
async def test_append_message_assigns_monotonic_ordinals(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    a = await svc.append_message(session, conversation_id=conv.id, role="user", content="one")
    b = await svc.append_message(session, conversation_id=conv.id, role="assistant", content="two")
    c = await svc.append_message(session, conversation_id=conv.id, role="user", content="three")
    assert (a.ordinal, b.ordinal, c.ordinal) == (1, 2, 3)


@pytest.mark.asyncio
async def test_list_messages_ordered_by_ordinal(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    await svc.append_message(session, conversation_id=conv.id, role="user", content="one")
    await svc.append_message(session, conversation_id=conv.id, role="assistant", content="two")
    msgs = await svc.list_messages(session, conv.id)
    assert [m.content for m in msgs] == ["one", "two"]


@pytest.mark.asyncio
async def test_append_message_persists_tool_payloads(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    msg = await svc.append_message(
        session,
        conversation_id=conv.id,
        role="assistant",
        content=None,
        tool_calls=[{"id": "tc1", "name": "list_accounts", "arguments": "{}"}],
        tool_result={"items": [{"id": "x"}]},
        citations=[{"doc_id": "d", "score": 0.9}],
        input_tokens=12,
        output_tokens=34,
    )
    assert msg.tool_calls == [{"id": "tc1", "name": "list_accounts", "arguments": "{}"}]
    assert msg.tool_result == {"items": [{"id": "x"}]}
    assert msg.citations == [{"doc_id": "d", "score": 0.9}]
    assert msg.input_tokens == 12
    assert msg.output_tokens == 34


@pytest.mark.asyncio
async def test_update_title_if_empty_only_writes_when_blank(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    # First call: title is None → should fill in.
    await svc.update_title_if_empty(session, conv.id, "Auto generated")
    refreshed = await svc.get_conversation(session, conv.id, test_workspace.id)

    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.title == "Auto generated"

    # Second call: title already set → should NOT overwrite.
    await svc.update_title_if_empty(session, conv.id, "Different title")
    refreshed = await svc.get_conversation(session, conv.id, test_workspace.id)

    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.title == "Auto generated"


@pytest.mark.asyncio
async def test_update_title_if_empty_silent_on_missing_conv(session):
    # No exception when the conv doesn't exist — used by streaming code.
    await svc.update_title_if_empty(session, uuid.uuid4(), "x")


@pytest.mark.asyncio
async def test_update_title_truncates_to_200_chars(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    long_title = "x" * 500
    out = await svc.update_title(session, conv.id, test_workspace.id,long_title)

    assert out is not None
    assert out.title is not None
    assert len(out.title) == 200


@pytest.mark.asyncio
async def test_update_title_returns_none_on_missing(session, test_workspace):
    out = await svc.update_title(session, uuid.uuid4(), test_workspace.id, "x")
    assert out is None


@pytest.mark.asyncio
async def test_update_title_blank_clears_to_none(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id, title="Existing",
    )
    out = await svc.update_title(session, conv.id, test_workspace.id,"   ")
    assert out is not None
    assert out.title is None


@pytest.mark.asyncio
async def test_delete_conversation_returns_false_on_missing(session, test_workspace):
    assert await svc.delete_conversation(session, uuid.uuid4(), test_workspace.id) is False


@pytest.mark.asyncio
async def test_delete_conversation_removes_row(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    assert await svc.delete_conversation(session, conv.id, test_workspace.id) is True
    assert await svc.get_conversation(session, conv.id, test_workspace.id) is None
