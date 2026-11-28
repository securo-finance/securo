"""The fifth usage limit: assistant messages per calendar month (#1968, ADR 0053).

Counted by ownership, from the messages people sent in the workspaces the
account created, in the instance's calendar month, and only the ones the
instance's own LLM connection answered. Checked only with MANAGED_INSTANCE and
the agents module on.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import get_agent_settings
from app.agents.models.agent import Agent
from app.agents.models.connection import LlmConnection
from app.agents.models.conversation import Conversation, Message
from app.agents.providers.base import ChatChunk, LLMError
from app.agents.runtime.executor import AgentExecutor
from app.core.app_clock import invalidate_timezone_cache
from app.core.config import get_settings
from app.models.app_settings import AppSetting
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import quota_service
from tests.test_agents_executor import _FakeMCP, _ScriptedProvider

ASSISTANT = quota_service.ASSISTANT_MESSAGES
NOVEMBER = date(2026, 11, 15)


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


async def _limit(session: AsyncSession, user_id: uuid.UUID, **values) -> None:
    await quota_service.set_quota(session, user_id, values)
    await session.commit()


async def _user(session: AsyncSession, email: str) -> User:
    user = User(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    return user


async def _workspace(session: AsyncSession, creator: User, *, archived: bool = False) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(), name="W", kind="personal", created_by_user_id=creator.id, is_archived=archived
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def _conversation(session: AsyncSession, workspace: Workspace, sender: User) -> Conversation:
    agent = Agent(id=uuid.uuid4(), user_id=sender.id, workspace_id=workspace.id, name="A")
    session.add(agent)
    await session.flush()
    conv = Conversation(
        id=uuid.uuid4(), user_id=sender.id, agent_id=agent.id, workspace_id=workspace.id, channel="web"
    )
    session.add(conv)
    await session.flush()
    return conv


async def _messages(
    session: AsyncSession,
    conv: Conversation,
    count: int,
    *,
    at: datetime = datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
    role: str = "user",
    instance_llm: bool = True,
) -> None:
    start = await session.scalar(
        select(Message.ordinal).where(Message.conversation_id == conv.id).order_by(Message.ordinal.desc()).limit(1)
    ) or 0
    for i in range(count):
        session.add(Message(
            conversation_id=conv.id, ordinal=start + i + 1, role=role, content="hi",
            created_at=at, instance_llm=instance_llm,
        ))
    await session.flush()


async def _count(session: AsyncSession, user: User, today: date = NOVEMBER) -> int:
    return await quota_service.count_assistant_messages(session, user.id, today=today)


# --- What is counted -------------------------------------------------------------


@pytest.mark.asyncio
async def test_counts_messages_across_every_workspace_the_owner_created(session: AsyncSession):
    owner = await _user(session, "owner@example.com")
    guest = await _user(session, "guest@example.com")
    first = await _conversation(session, await _workspace(session, owner), owner)
    second_ws = await _workspace(session, owner)
    second = await _conversation(session, second_ws, guest)
    session.add(WorkspaceMember(workspace_id=second_ws.id, user_id=guest.id, role="editor"))
    await _messages(session, first, 2)
    # Whoever in the workspace sent it, the message fills the owner's number.
    await _messages(session, second, 3)

    assert await _count(session, owner) == 5
    assert await _count(session, guest) == 0


@pytest.mark.asyncio
async def test_a_workspace_someone_invited_you_into_never_counts(session: AsyncSession):
    me = await _user(session, "me@example.com")
    other = await _user(session, "other@example.com")
    theirs = await _workspace(session, other)
    session.add(WorkspaceMember(workspace_id=theirs.id, user_id=me.id, role="editor"))
    await _messages(session, await _conversation(session, theirs, me), 4)

    assert await _count(session, me) == 0
    assert await _count(session, other) == 4


@pytest.mark.asyncio
async def test_only_what_a_person_sends_counts(session: AsyncSession):
    """Replies, tool calls and tool results are the same turn, not new messages."""
    owner = await _user(session, "owner@example.com")
    conv = await _conversation(session, await _workspace(session, owner), owner)
    await _messages(session, conv, 1)
    await _messages(session, conv, 3, role="assistant")
    await _messages(session, conv, 2, role="tool")

    assert await _count(session, owner) == 1


@pytest.mark.asyncio
async def test_messages_answered_by_the_accounts_own_connection_are_free(session: AsyncSession):
    owner = await _user(session, "owner@example.com")
    conv = await _conversation(session, await _workspace(session, owner), owner)
    await _messages(session, conv, 2)
    await _messages(session, conv, 5, instance_llm=False)

    assert await _count(session, owner) == 2


@pytest.mark.asyncio
async def test_archiving_a_workspace_does_not_give_spent_messages_back(session: AsyncSession):
    owner = await _user(session, "owner@example.com")
    conv = await _conversation(session, await _workspace(session, owner, archived=True), owner)
    await _messages(session, conv, 2)

    assert await _count(session, owner) == 2


@pytest.mark.asyncio
async def test_the_month_follows_the_instance_timezone(session: AsyncSession):
    """23:30 on 31 October in São Paulo is already November in UTC: it is October's."""
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.flush()
    invalidate_timezone_cache()
    owner = await _user(session, "owner@example.com")
    conv = await _conversation(session, await _workspace(session, owner), owner)
    await _messages(session, conv, 1, at=datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc))
    await _messages(session, conv, 2, at=datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc))
    await _messages(session, conv, 4, at=datetime(2026, 12, 1, 3, 30, tzinfo=timezone.utc))

    try:
        assert await _count(session, owner, date(2026, 11, 30)) == 2
        assert await _count(session, owner, date(2026, 10, 31)) == 1
        assert await _count(session, owner, date(2026, 12, 1)) == 4
    finally:
        invalidate_timezone_cache()


@pytest.mark.asyncio
async def test_check_fit_reports_the_months_messages(session: AsyncSession):
    owner = await _user(session, "owner@example.com")
    conv = await _conversation(session, await _workspace(session, owner), owner)
    await _messages(session, conv, 3, at=datetime.now(timezone.utc))

    fit = await quota_service.check_fit(session, owner.id, {ASSISTANT: 1})
    counter = next(c for c in fit.counters if c.counter == ASSISTANT)
    assert (counter.current, counter.target, counter.excess) == (3, 1, 2)
    assert fit.allowed is False


# --- The chat gate ---------------------------------------------------------------


class _RecordingExecutor:
    """Stands in for the runtime: the gate is what is under test here."""

    calls: list[dict[str, Any]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def run(self, **kwargs) -> AsyncIterator[Any]:
        _RecordingExecutor.calls.append(kwargs)
        return
        yield  # pragma: no cover


@pytest.fixture
def recorded():
    _RecordingExecutor.calls = []
    with patch("app.agents.api.chat.AgentExecutor", _RecordingExecutor):
        yield _RecordingExecutor.calls


async def _sent_this_month(session: AsyncSession, test_agent: Agent, test_user: User, count: int) -> None:
    conv = Conversation(
        id=uuid.uuid4(), user_id=test_user.id, agent_id=test_agent.id,
        workspace_id=test_agent.workspace_id, channel="web",
    )
    session.add(conv)
    await session.flush()
    await _messages(session, conv, count, at=datetime.now(timezone.utc))
    await session.commit()


def _chat(test_agent: Agent) -> str:
    return f"/api/agents/{test_agent.id}/chat"


@pytest.mark.asyncio
async def test_off_without_the_switch(
    recorded, client: AsyncClient, session, test_user, test_agent, auth_headers
):
    await _limit(session, test_user.id, **{ASSISTANT: 0})
    resp = await client.post(_chat(test_agent), json={"content": "hi"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_off_with_the_agents_module_off(managed, monkeypatch, session, test_user, test_agent):
    monkeypatch.setattr(get_agent_settings(), "enabled", False)
    await _limit(session, test_user.id, **{ASSISTANT: 0})
    await quota_service.check_assistant_message(session, test_agent.workspace_id, instance_llm=True)


@pytest.mark.asyncio
async def test_the_chat_refuses_past_the_limit(
    managed, recorded, client: AsyncClient, session, test_user, test_agent, auth_headers
):
    await _limit(session, test_user.id, **{ASSISTANT: 2})
    await _sent_this_month(session, test_agent, test_user, 2)
    before = (await session.scalars(select(Conversation.id))).all()

    resp = await client.post(_chat(test_agent), json={"content": "hi"}, headers=auth_headers)

    assert resp.status_code == 403
    assert resp.json()["detail"] | {"message": None} == {
        "code": "quota_exceeded", "counter": ASSISTANT, "current": 2, "maximum": 2, "message": None,
    }
    assert recorded == []
    # Refused before anything is written: no empty conversation is left behind.
    assert (await session.scalars(select(Conversation.id))).all() == before


@pytest.mark.asyncio
async def test_the_chat_passes_under_the_limit_and_marks_the_message(
    managed, recorded, client: AsyncClient, session, test_user, test_agent, auth_headers
):
    await _limit(session, test_user.id, **{ASSISTANT: 2})
    await _sent_this_month(session, test_agent, test_user, 1)

    resp = await client.post(_chat(test_agent), json={"content": "hi"}, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert recorded[0]["instance_llm"] is True


@pytest.mark.asyncio
async def test_an_agent_on_the_accounts_own_connection_is_never_refused(
    managed, recorded, client: AsyncClient, session, test_user, test_agent, auth_headers
):
    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="Mine", kind="openai", is_default=True, extra={},
    ))
    await session.commit()
    await _limit(session, test_user.id, **{ASSISTANT: 0})

    resp = await client.post(_chat(test_agent), json={"content": "hi"}, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert recorded[0]["instance_llm"] is False


@pytest.mark.asyncio
async def test_a_member_spends_the_owners_number(
    managed, recorded, client: AsyncClient, session, test_user, test_agent, auth_headers
):
    """The workspace's creator holds the limit, so a member's send is refused on it."""
    owner = await _user(session, "owner@example.com")
    theirs = await _workspace(session, owner)
    session.add(WorkspaceMember(workspace_id=theirs.id, user_id=test_user.id, role="editor"))
    agent = Agent(id=uuid.uuid4(), user_id=test_user.id, workspace_id=theirs.id, name="Theirs")
    session.add(agent)
    await session.commit()
    await _limit(session, owner.id, **{ASSISTANT: 0})

    resp = await client.post(
        f"/api/agents/{agent.id}/chat", json={"content": "hi"},
        headers={**auth_headers, "X-Workspace-Id": str(theirs.id)},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["counter"] == ASSISTANT


# --- The runtime keeps the fact the count reads ------------------------------------


@pytest.mark.asyncio
async def test_the_executor_marks_the_persons_message_and_nothing_else(
    session, test_user, test_agent: Agent, test_conversation: Conversation
):
    provider = _ScriptedProvider([[
        ChatChunk(type="text_delta", text="Hello"),
        ChatChunk(type="finish", finish_reason="stop"),
    ]])
    executor = AgentExecutor(mcp=_FakeMCP(tools=[]))
    with patch("app.agents.runtime.executor._provider_for", return_value=provider):
        [ev async for ev in executor.run(
            session=session, agent=test_agent, user_id=test_user.id,
            conversation_id=test_conversation.id, user_message="hi", instance_llm=True,
        )]

    rows = (await session.execute(
        select(Message.role, Message.instance_llm).where(Message.conversation_id == test_conversation.id)
    )).all()
    assert ("user", True) in rows
    assert all(not flag for role, flag in rows if role != "user")


async def _run(session, test_user, test_agent, test_conversation, *, instance_llm: bool) -> list:
    executor = AgentExecutor(mcp=_FakeMCP(tools=[]))
    return [ev async for ev in executor.run(
        session=session, agent=test_agent, user_id=test_user.id,
        conversation_id=test_conversation.id, user_message="hi", instance_llm=instance_llm,
    )]


async def _user_flags(session, conversation: Conversation) -> list[bool]:
    return list((await session.scalars(
        select(Message.instance_llm).where(Message.conversation_id == conversation.id, Message.role == "user")
    )).all())


@pytest.mark.asyncio
async def test_a_message_without_a_model_to_answer_it_is_not_spent(
    session, test_user, test_agent: Agent, test_conversation: Conversation
):
    provider = _ScriptedProvider([])
    with patch(
        "app.agents.runtime.executor._provider_and_model_for", AsyncMock(return_value=(provider, ""))
    ):
        events = await _run(session, test_user, test_agent, test_conversation, instance_llm=True)

    assert any(ev.type == "error" and ev.error_code == "config" for ev in events)
    assert await _user_flags(session, test_conversation) == [False]


class _FailingProvider(_ScriptedProvider):
    async def chat_stream(  # type: ignore[override]
        self, messages, *, model, tools=None, temperature=0.4, max_tokens=None
    ) -> AsyncIterator[ChatChunk]:
        raise LLMError("upstream down")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_a_message_the_instance_model_failed_to_answer_is_not_spent(
    session, test_user, test_agent: Agent, test_conversation: Conversation
):
    with patch("app.agents.runtime.executor._provider_for", return_value=_FailingProvider([])):
        events = await _run(session, test_user, test_agent, test_conversation, instance_llm=True)

    assert any(ev.type == "error" for ev in events)
    assert await _user_flags(session, test_conversation) == [False]


@pytest.mark.asyncio
async def test_a_broken_own_connection_never_falls_back_to_the_instance_model(
    session, test_user, test_agent: Agent, test_conversation: Conversation
):
    fallback = _ScriptedProvider([[ChatChunk(type="finish", finish_reason="stop")]])
    with patch(
        "app.agents.runtime.executor._provider_and_model_for", AsyncMock(side_effect=RuntimeError("bad key"))
    ), patch("app.agents.runtime.executor._provider_for", return_value=fallback) as legacy:
        events = await _run(session, test_user, test_agent, test_conversation, instance_llm=False)

    assert [ev.type for ev in events][-2:] == ["error", "done"]
    assert events[-2].error_code == "config"
    legacy.assert_not_called()
    assert fallback.requests == []
    assert await _user_flags(session, test_conversation) == [False]


@pytest.mark.asyncio
async def test_an_openai_compatible_connection_keeps_its_endpoint(client: AsyncClient, auth_headers):
    """Clearing the address would send its messages to the instance's model, uncounted."""
    created = await client.post("/api/agents/connections", json={
        "name": "Local", "kind": "openai_compatible", "base_url": "http://llm.example.com/v1",
    }, headers=auth_headers)
    assert created.status_code in (200, 201), created.text

    resp = await client.patch(
        f"/api/agents/connections/{created.json()['id']}", json={"base_url": "  "}, headers=auth_headers
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "openai_compatible requires base_url"
