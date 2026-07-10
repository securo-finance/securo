"""Coverage-focused tests for workspace_service.

Targets get_managed_workspaces, get_default_workspace manager fallback,
create_workspace with self_membership + seed_defaults, add_member error
paths, update_member_role validation, get_workspace_stats, archive_workspace,
and remove_member.
"""

import uuid

import bcrypt as _bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace
from app.services import workspace_service


async def _make_user(session, email):
    hashed = _bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_verified=True,
        preferences={"currency_display": "USD", "language": "en"},
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_get_managed_workspaces(session: AsyncSession, test_user: User):
    ws = await workspace_service.create_workspace(
        session, name="Managed Co", creator=test_user, seed_defaults=False
    )
    await session.commit()
    managed = await workspace_service.get_managed_workspaces(session, test_user.id)
    assert ws.id in {w.id for w in managed}


@pytest.mark.asyncio
async def test_get_default_workspace_member_first(session: AsyncSession, test_user: User):
    ws = await workspace_service.get_default_workspace(session, test_user.id)
    assert ws is not None
    assert ws.kind == "personal"


@pytest.mark.asyncio
async def test_get_default_workspace_falls_back_to_managed(session: AsyncSession):
    """A manager-only user (no memberships) falls back to a managed workspace."""
    manager = await _make_user(session, "manageronly@example.com")
    ws = Workspace(
        id=uuid.uuid4(),
        name="OnlyManaged",
        kind="personal",
        created_by_user_id=manager.id,
        managed_by_user_id=manager.id,
        default_currency="USD",
    )
    session.add(ws)
    await session.commit()

    default = await workspace_service.get_default_workspace(session, manager.id)
    assert default is not None
    assert default.id == ws.id


@pytest.mark.asyncio
async def test_get_default_workspace_none(session: AsyncSession):
    stranger = await _make_user(session, "nows@example.com")
    await session.commit()
    assert await workspace_service.get_default_workspace(session, stranger.id) is None


@pytest.mark.asyncio
async def test_create_workspace_with_self_membership_and_seed(
    session: AsyncSession, test_user: User
):
    ws = await workspace_service.create_workspace(
        session,
        name="Day To Day",
        creator=test_user,
        self_membership=True,
        seed_defaults=True,
        icon="briefcase",
        color="#112233",
    )
    await session.commit()

    assert ws.icon == "briefcase"
    assert ws.color == "#112233"
    members = await workspace_service.list_members(session, ws.id)
    assert len(members) == 1
    assert members[0][0].role == "owner"

    # seed_defaults created categories for the workspace.
    from app.models.category import Category

    cats = await session.execute(select(Category).where(Category.workspace_id == ws.id))
    assert len(cats.scalars().all()) > 0


@pytest.mark.asyncio
async def test_create_workspace_blank_name_fallback(session: AsyncSession, test_user: User):
    ws = await workspace_service.create_workspace(
        session, name="   ", creator=test_user, seed_defaults=False
    )
    await session.commit()
    assert ws.name == "Workspace"


@pytest.mark.asyncio
async def test_add_member_invalid_role(session: AsyncSession, test_user: User, test_workspace):
    other = await _make_user(session, "addinvalid@example.com")
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await workspace_service.add_member(session, test_workspace.id, other.id, role="superadmin")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_add_member_duplicate(session: AsyncSession, test_user: User, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.add_member(session, test_workspace.id, test_user.id, role="editor")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_member_role_invalid(session: AsyncSession, test_user: User, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.update_member_role(session, test_workspace.id, test_user.id, "boss")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_member_role_member_not_found(session: AsyncSession, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.update_member_role(
            session, test_workspace.id, uuid.uuid4(), "editor"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_member_role_demote_with_other_owner(
    session: AsyncSession, test_user: User, test_workspace
):
    other = await _make_user(session, "coowner@example.com")
    await session.commit()
    await workspace_service.add_member(session, test_workspace.id, other.id, role="owner")
    await session.commit()
    # Now demoting test_user is allowed since another owner exists.
    member = await workspace_service.update_member_role(
        session, test_workspace.id, test_user.id, "editor"
    )
    await session.commit()
    assert member.role == "editor"


@pytest.mark.asyncio
async def test_get_workspace_stats(
    session: AsyncSession, test_user: User, test_workspace, test_account, test_transactions
):
    stats = await workspace_service.get_workspace_stats(session, test_workspace.id)
    assert stats["members"] == 1
    assert stats["accounts"] >= 1
    assert stats["transactions"] >= 1


@pytest.mark.asyncio
async def test_archive_workspace_blocks_last(
    session: AsyncSession, test_user: User, test_workspace
):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.archive_workspace(session, test_workspace.id, test_user.id)
    assert exc.value.status_code == 400
    assert "last workspace" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_archive_workspace_succeeds_with_other(
    session: AsyncSession, test_user: User, test_workspace
):
    await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True, seed_defaults=False
    )
    await session.commit()
    archived = await workspace_service.archive_workspace(session, test_workspace.id, test_user.id)
    await session.commit()
    assert archived.is_archived is True

    # Archiving an already-archived workspace is a no-op return.
    again = await workspace_service.archive_workspace(session, test_workspace.id, test_user.id)
    assert again.is_archived is True


@pytest.mark.asyncio
async def test_archive_workspace_not_found(session: AsyncSession, test_user: User):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.archive_workspace(session, uuid.uuid4(), test_user.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_not_found(session: AsyncSession, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.remove_member(session, test_workspace.id, uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_non_owner_succeeds(
    session: AsyncSession, test_user: User, test_workspace
):
    other = await _make_user(session, "removeme@example.com")
    await session.commit()
    await workspace_service.add_member(session, test_workspace.id, other.id, role="editor")
    await session.commit()
    await workspace_service.remove_member(session, test_workspace.id, other.id)
    await session.commit()
    assert await workspace_service.get_membership(session, test_workspace.id, other.id) is None


@pytest.mark.asyncio
async def test_remove_sole_owner_blocked(session: AsyncSession, test_user: User, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.remove_member(session, test_workspace.id, test_user.id)
    assert exc.value.status_code == 400
    assert "sole owner" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_remove_owner_succeeds_with_co_owner(
    session: AsyncSession, test_user: User, test_workspace
):
    other = await _make_user(session, "coowner2@example.com")
    await session.commit()
    await workspace_service.add_member(session, test_workspace.id, other.id, role="owner")
    await session.commit()
    # With a second owner, removing one owner is allowed.
    await workspace_service.remove_member(session, test_workspace.id, other.id)
    await session.commit()
    assert await workspace_service.get_membership(session, test_workspace.id, other.id) is None


@pytest.mark.asyncio
async def test_demote_sole_owner_blocked(session: AsyncSession, test_user: User, test_workspace):
    with pytest.raises(HTTPException) as exc:
        await workspace_service.update_member_role(
            session, test_workspace.id, test_user.id, "viewer"
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_members_empty(session: AsyncSession):
    ws = Workspace(
        id=uuid.uuid4(),
        name="EmptyWs",
        kind="personal",
        default_currency="USD",
    )
    session.add(ws)
    await session.commit()
    assert await workspace_service.list_members(session, ws.id) == []


@pytest.mark.asyncio
async def test_is_workspace_manager(session: AsyncSession, test_user: User):
    ws = await workspace_service.create_workspace(
        session, name="MgrCheck", creator=test_user, seed_defaults=False
    )
    await session.commit()
    assert await workspace_service.is_workspace_manager(session, ws.id, test_user.id) is True
    assert await workspace_service.is_workspace_manager(session, ws.id, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_get_membership_none(session: AsyncSession, test_workspace):
    assert await workspace_service.get_membership(session, test_workspace.id, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_require_membership_manager_path_and_role_floor(session: AsyncSession):
    """A manager (no membership row) gets virtual owner access and passes
    the owner floor; a stranger is rejected with 404."""
    manager = await _make_user(session, "mgrfloor@example.com")
    ws = Workspace(
        id=uuid.uuid4(),
        name="Mgr",
        kind="personal",
        created_by_user_id=manager.id,
        managed_by_user_id=manager.id,
        default_currency="USD",
    )
    session.add(ws)
    await session.commit()

    member = await workspace_service.require_membership(session, ws.id, manager.id)
    assert member.role == "manager"

    member = await workspace_service.require_membership(
        session, ws.id, manager.id, min_role="owner"
    )
    assert member.role == "manager"

    with pytest.raises(HTTPException) as exc:
        await workspace_service.require_membership(session, ws.id, uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_membership_viewer_role_floor_403(
    session: AsyncSession, test_user: User, test_workspace
):
    viewer = await _make_user(session, "vfloor@example.com")
    await session.commit()
    await workspace_service.add_member(session, test_workspace.id, viewer.id, role="viewer")
    await session.commit()
    # Viewer trying for editor floor -> 403.
    with pytest.raises(HTTPException) as exc:
        await workspace_service.require_membership(
            session, test_workspace.id, viewer.id, min_role="editor"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_personal_workspace_english_name(session: AsyncSession):
    """A non-pt user gets the English 'Personal' name; idempotent on repeat."""
    user = await _make_user(session, "english@example.com")
    user.preferences = {"language": "en", "currency_display": "USD"}
    await session.flush()
    ws1 = await workspace_service.create_personal_workspace_for_user(session, user, commit=True)
    assert ws1.name == "Personal"
    # Second call returns the existing one (idempotent found branch).
    ws2 = await workspace_service.create_personal_workspace_for_user(session, user, commit=True)
    assert ws2.id == ws1.id
