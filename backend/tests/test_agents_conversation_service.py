"""Direct unit tests for app/agents/services/conversation_service. The
HTTP layer covers most paths in test_agents_api.py — this fills in the
service-only utilities (append_message ordinal generation,
update_title_if_empty, etc.) that don't ride a route."""
from __future__ import annotations

import uuid

import pytest

from app.agents.services import conversation_service as svc


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
async def test_update_title_blank_clears_to_none(session, test_user, test_workspace, test_agent):
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id, title="Existing",
    )
    out = await svc.update_title(session, conv.id, test_workspace.id,"   ")
    assert out is not None
    assert out.title is None


@pytest.mark.asyncio
async def test_list_messages_window_keeps_newest_turns(session, test_user, test_workspace, test_agent):
    """Regression: with more messages than `limit`, the returned window must
    end at the NEWEST message. The executor replays this window to the LLM —
    when the limit selected the oldest rows instead, the model never saw the
    user's latest question in long conversations and re-answered stale ones."""
    conv = await svc.create_conversation(
        session, workspace_id=test_workspace.id, user_id=test_user.id, agent_id=test_agent.id,
    )
    for i in range(30):
        await svc.append_message(
            session, conversation_id=conv.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"msg-{i}",
        )

    msgs = await svc.list_messages(session, conv.id, limit=10)

    assert len(msgs) == 10
    assert [m.content for m in msgs] == [f"msg-{i}" for i in range(20, 30)]
    ordinals = [m.ordinal for m in msgs]
    assert ordinals == sorted(ordinals)
