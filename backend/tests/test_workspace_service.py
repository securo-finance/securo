"""Unit tests for the workspace service + membership helpers.

Covers the happy paths the registration flow + invite API rely on:
  - Personal workspace auto-creation is idempotent
  - Membership listing returns the right shape
  - Role floor enforcement (require_membership(min_role=...))
  - Manager virtual access (managed_by_user_id)
  - Sole-owner protection on demote + remove
"""

import uuid

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.models.workspace import Workspace
from app.services import workspace_service


@pytest.mark.asyncio
async def test_create_personal_workspace_is_idempotent(session, test_user: User):
    """Calling twice returns the same workspace, doesn't duplicate."""
    ws1 = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    ws2 = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    assert ws1.id == ws2.id

    workspaces = await workspace_service.get_user_workspaces(session, test_user.id)
    assert len(workspaces) == 1
    assert workspaces[0].kind == "personal"


@pytest.mark.asyncio
async def test_personal_workspace_localizes_name_from_user_prefs(session, test_user: User):
    """Portuguese-speaking user gets 'Pessoal' not 'Personal'."""
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    # test_user has language='pt-BR' in conftest
    assert ws.name == "Pessoal"
    assert ws.default_currency == "BRL"


@pytest.mark.asyncio
async def test_require_membership_enforces_role_floor(session, test_user: User):
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()

    # Owner-floor passes for the owner.
    member = await workspace_service.require_membership(
        session, ws.id, test_user.id, min_role="owner"
    )
    assert member.role == "owner"

    # Non-member gets 404, not 403 (don't leak existence).
    stranger_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await workspace_service.require_membership(session, ws.id, stranger_id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_viewer_role_blocks_write_min_role(session, test_user: User):
    """A viewer's require_membership(min_role='editor') raises 403."""
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()

    # Add a second user as a viewer.
    other = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(other)
    await session.flush()
    await workspace_service.add_member(session, workspace_id=ws.id, user_id=other.id, role="viewer")
    await session.commit()

    # Viewer can read.
    member = await workspace_service.require_membership(session, ws.id, other.id, min_role="viewer")
    assert member.role == "viewer"

    # But not write.
    with pytest.raises(HTTPException) as exc:
        await workspace_service.require_membership(session, ws.id, other.id, min_role="editor")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_manager_gets_virtual_owner_access(session, test_user: User):
    """A user named on managed_by_user_id gets manager role without a membership row."""
    # Create a workspace whose manager is `test_user` but no membership row.
    ws = Workspace(
        name="Managed",
        kind="personal",
        created_by_user_id=test_user.id,
        managed_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(ws)
    await session.commit()

    # No `workspace_members` row exists for test_user; require_membership
    # should still succeed via the manager path.
    member = await workspace_service.require_membership(session, ws.id, test_user.id)
    assert member.role == "manager"

    # Manager passes the owner-floor.
    member = await workspace_service.require_membership(
        session, ws.id, test_user.id, min_role="owner"
    )
    assert member.role == "manager"


@pytest.mark.asyncio
async def test_cannot_demote_sole_owner(session, test_user: User):
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await workspace_service.update_member_role(session, ws.id, test_user.id, "viewer")
    assert exc.value.status_code == 400
    assert "sole owner" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_cannot_remove_sole_owner(session, test_user: User):
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await workspace_service.remove_member(session, ws.id, test_user.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_members_includes_invited_users(session, test_user: User):
    ws = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()

    other = User(
        id=uuid.uuid4(),
        email="editor@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(other)
    await session.flush()
    await workspace_service.add_member(session, workspace_id=ws.id, user_id=other.id, role="editor")
    await session.commit()

    members = await workspace_service.list_members(session, ws.id)
    assert len(members) == 2
    emails = {u.email for _, u in members}
    assert emails == {"test@example.com", "editor@example.com"}


@pytest.mark.asyncio
async def test_create_workspace_makes_creator_the_manager(session, test_user: User):
    ws = await workspace_service.create_workspace(
        session,
        name="Client books",
        creator=test_user,
        kind="personal",
    )
    await session.commit()

    assert ws.managed_by_user_id == test_user.id
    assert ws.created_by_user_id == test_user.id
    # Default `self_membership=False` means no member row was added.
    members = await workspace_service.list_members(session, ws.id)
    assert len(members) == 0

    # And yet the creator can access via the manager path.
    member = await workspace_service.require_membership(session, ws.id, test_user.id)
    assert member.role == "manager"
