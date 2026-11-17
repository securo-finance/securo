"""MCP token scoping.

Two guarantees:
  - A token the agent runtime sends to a user-supplied MCP server is never
    accepted by the built-in server, so that server cannot replay it to read
    the workspace.
  - The built-in server re-checks the caller against the database on every
    tool call: a token stays valid for months, but a user removed from a
    workspace, demoted to viewer, or deactivated loses access right away.
"""
import uuid
from typing import Any, cast

import pytest
from jose import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

import mcp_server.tools  # noqa: F401  (registers the tools)
from app.agents.mcp.auth import extra_server_audience, mint_token
from app.agents.mcp.client import BUILTIN_SERVER_NAME, MCPClient, MCPRegistry, ToolHandle
from app.models.rule import Rule
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext, ToolAccessDenied, authorize_call, verify_request
from mcp_server.registry import call_tool


def _bearer(token: str) -> Request:
    return Request(scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


# --- Tokens for extra servers -----------------------------------------------


def test_extra_server_token_is_rejected_by_builtin_server():
    token = mint_token(user_id=uuid.uuid4(), audience=extra_server_audience("notes"))
    with pytest.raises(HTTPException) as exc:
        verify_request(_bearer(token))
    assert exc.value.status_code == 401


def test_builtin_token_still_verifies():
    user_id = uuid.uuid4()
    ctx = verify_request(_bearer(mint_token(user_id=user_id)))
    assert ctx.user_id == user_id


class _RecordingClient:
    def __init__(self, name: str):
        self.name = name
        self.url = f"http://{name}"
        self.tokens: list[str] = []

    async def list_tools(self, *, token: str) -> list[ToolHandle]:
        self.tokens.append(token)
        return [ToolHandle(server=self.name, name=f"{self.name}_tool", description="", parameters={})]

    async def call_tool(self, *, name: str, arguments: dict[str, Any], token: str) -> dict[str, Any]:
        self.tokens.append(token)
        return {"ok": True}


def _registry_with(*names: str) -> tuple[MCPRegistry, dict[str, _RecordingClient]]:
    registry = MCPRegistry.__new__(MCPRegistry)
    clients = {n: _RecordingClient(n) for n in names}
    registry._servers = cast("dict[str, MCPClient]", clients)
    return registry, clients


@pytest.mark.asyncio
async def test_discover_sends_each_server_its_own_token():
    registry, clients = _registry_with(BUILTIN_SERVER_NAME, "notes", "calendar")
    user_id = uuid.uuid4()
    await registry.discover(user_id=user_id, workspace_id=uuid.uuid4())

    verify_request(_bearer(clients[BUILTIN_SERVER_NAME].tokens[0]))
    for name in ("notes", "calendar"):
        with pytest.raises(HTTPException):
            verify_request(_bearer(clients[name].tokens[0]))

    # Two extra servers never share an audience, so one can't replay the
    # other's token either. Checked on discovery and on a call.
    await registry.call(wire_name="notes__notes_tool", arguments={}, user_id=user_id)
    await registry.call(wire_name="calendar__calendar_tool", arguments={}, user_id=user_id)
    for name in ("notes", "calendar"):
        audiences = {jwt.get_unverified_claims(t)["aud"] for t in clients[name].tokens}
        assert audiences == {extra_server_audience(name)}


@pytest.mark.asyncio
async def test_call_sends_extra_server_a_token_the_builtin_rejects():
    registry, clients = _registry_with(BUILTIN_SERVER_NAME, "notes")
    user_id = uuid.uuid4()

    await registry.call(wire_name="notes__notes_tool", arguments={}, user_id=user_id)
    with pytest.raises(HTTPException):
        verify_request(_bearer(clients["notes"].tokens[-1]))

    # The bare-name fallback lists tools on every server: each still gets
    # only its own token.
    await registry.call(wire_name="notes_tool", arguments={}, user_id=user_id)
    verify_request(_bearer(clients[BUILTIN_SERVER_NAME].tokens[-1]))
    for token in clients["notes"].tokens:
        with pytest.raises(HTTPException):
            verify_request(_bearer(token))


def test_extra_server_cannot_take_the_builtin_name(monkeypatch):
    from app.agents import config
    from app.agents.mcp.client import _parse_servers

    config.get_agent_settings.cache_clear()
    monkeypatch.setenv("AGENTS_EXTRA_MCP_SERVERS", "http://evil.example/mcp|securo,http://notes.example/mcp|notes")
    try:
        specs = _parse_servers()
    finally:
        monkeypatch.delenv("AGENTS_EXTRA_MCP_SERVERS", raising=False)
        config.get_agent_settings.cache_clear()

    builtin = [s for s in specs if s.name == BUILTIN_SERVER_NAME]
    assert len(builtin) == 1
    assert "evil.example" not in builtin[0].url
    assert [s.name for s in specs] == [BUILTIN_SERVER_NAME, "notes"]


# --- Per-call membership and role checks --------------------------------------


async def _membership(session: AsyncSession, user: User, workspace: Workspace) -> WorkspaceMember:
    return (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.workspace_id == workspace.id,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_member_is_authorized_with_their_role(session, test_user, test_workspace):
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    assert await authorize_call(session, ctx) == test_workspace.id
    assert ctx.role == "owner"
    assert ctx.can_write


@pytest.mark.asyncio
async def test_token_without_workspace_is_pinned_to_the_default(session, test_user, test_workspace):
    ctx = CallContext(user_id=test_user.id)
    assert await authorize_call(session, ctx) == test_workspace.id
    assert ctx.workspace_id == test_workspace.id


@pytest.mark.asyncio
async def test_removed_member_is_denied(session, test_user, test_workspace):
    await session.delete(await _membership(session, test_user, test_workspace))
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    with pytest.raises(ToolAccessDenied):
        await authorize_call(session, ctx)


@pytest.mark.asyncio
async def test_workspace_of_someone_else_is_denied(session, test_user):
    other = Workspace(id=uuid.uuid4(), name="Other", kind="personal", default_currency="USD", locale="en")
    session.add(other)
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=other.id, external=True)
    with pytest.raises(ToolAccessDenied):
        await authorize_call(session, ctx)


@pytest.mark.asyncio
async def test_archived_workspace_is_denied(session, test_user, test_workspace):
    test_workspace.is_archived = True
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    with pytest.raises(ToolAccessDenied):
        await authorize_call(session, ctx)


@pytest.mark.asyncio
async def test_inactive_user_is_denied(session, test_user, test_workspace):
    test_user.is_active = False
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    with pytest.raises(ToolAccessDenied):
        await authorize_call(session, ctx)


@pytest.mark.asyncio
async def test_manager_is_authorized_as_manager(session, test_user):
    managed = Workspace(
        id=uuid.uuid4(),
        name="Managed",
        kind="personal",
        default_currency="USD",
        locale="en",
        managed_by_user_id=test_user.id,
    )
    session.add(managed)
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=managed.id, external=True)
    assert await authorize_call(session, ctx) == managed.id
    assert ctx.role == "manager"
    assert ctx.can_write


_RULE_ARGS = {
    "name": "Coffee",
    "conditions": [{"field": "description", "op": "contains", "value": "coffee"}],
    "actions": [{"op": "ignore", "value": True}],
    "apply_to_existing": False,
}


@pytest.mark.asyncio
async def test_demoted_viewer_cannot_apply(session, test_user, test_workspace):
    member = await _membership(session, test_user, test_workspace)
    member.role = "viewer"
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    with pytest.raises(ToolAccessDenied):
        await call_tool(session, ctx, "propose_create_rule", {**_RULE_ARGS, "apply": True})

    # Reading and previewing still work for a viewer.
    preview = await call_tool(session, ctx, "propose_create_rule", dict(_RULE_ARGS))
    assert preview["kind"] == "create_rule"
    rules = (await session.execute(select(Rule).where(Rule.workspace_id == test_workspace.id))).scalars().all()
    assert all(r.name != "Coffee" for r in rules)


@pytest.mark.asyncio
async def test_viewer_role_blocks_the_apply_gate_directly():
    from mcp_server.tools.proposals import _can_apply

    viewer = CallContext(user_id=uuid.uuid4(), external=True, role="viewer")
    editor = CallContext(user_id=uuid.uuid4(), external=True, role="editor")
    assert not _can_apply(viewer, True)
    assert _can_apply(editor, True)


@pytest.mark.asyncio
async def test_editor_can_apply(session, test_user, test_workspace):
    member = await _membership(session, test_user, test_workspace)
    member.role = "editor"
    await session.commit()

    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    result = await call_tool(session, ctx, "propose_create_rule", {**_RULE_ARGS, "apply": True})
    assert result["applied"] is True
