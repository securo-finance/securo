"""Cross-user visibility for groups: an owner can see/edit, a linked
member can see, others see nothing. Settlement and balance reads work
for linked members; writes are owner-only."""

import uuid
from datetime import date
from decimal import Decimal

import bcrypt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate
from app.services import (
    balance_service,
    group_service,
    settlement_service,
    workspace_service,
)


async def _make_user(session: AsyncSession, email: str) -> User:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_user_with_workspace(
    session: AsyncSession, email: str
) -> tuple[User, Workspace]:
    user = await _make_user(session, email)
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


@pytest.mark.asyncio
async def test_email_auto_resolves_to_linked_user(
    session: AsyncSession, test_user, test_workspace
):
    other = await _make_user(session, "friend@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    member = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Friend", email="friend@example.com"),
    )

    assert member is not None
    assert member.linked_user_id == other.id


@pytest.mark.asyncio
async def test_email_unknown_creates_shadow(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    member = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Stranger", email="nobody@example.org"),
    )

    assert member is not None
    assert member.linked_user_id is None
    assert member.email == "nobody@example.org"


@pytest.mark.asyncio
async def test_linked_member_sees_group_in_list(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Viewer", email="viewer@example.com"),
    )

    visible = await group_service.list_groups(session, other_ws.id, other.id)
    assert {g.id for g in visible} == {group.id}
    # Linked member sees is_owner=False; the original owner sees True.
    assert visible[0].is_owner is False

    owners_view = await group_service.list_groups(
        session, test_workspace.id, test_user.id
    )
    assert owners_view[0].is_owner is True


@pytest.mark.asyncio
async def test_unrelated_user_does_not_see_group(
    session: AsyncSession, test_user, test_workspace
):
    stranger, stranger_ws = await _make_user_with_workspace(
        session, "stranger@example.com"
    )
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Private")
    )

    visible = await group_service.list_groups(session, stranger_ws.id, stranger.id)
    assert visible == []


@pytest.mark.asyncio
async def test_visible_get_works_for_linked_member(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    via_visible = await group_service.get_group_visible(
        session, group.id, other_ws.id, other.id
    )
    assert via_visible is not None
    assert via_visible.is_owner is False

    # Owner-only get returns None when scoped to the linked member's
    # workspace — edit paths reject non-owners cleanly.
    via_owned = await group_service.get_group(session, group.id, other_ws.id)
    assert via_owned is None


@pytest.mark.asyncio
async def test_linked_member_cannot_modify_group(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    # Updates from the linked member's own workspace resolve to None (no-op).
    from app.schemas.group import GroupUpdate

    updated = await group_service.update_group(
        session, group.id, other_ws.id, other.id, GroupUpdate(name="Hijacked")
    )
    assert updated is None

    # Member-add scoped to the linked viewer's workspace is also rejected.
    add = await group_service.create_member(
        session, group.id, other_ws.id, GroupMemberCreate(name="Bob")
    )
    assert add is None


@pytest.mark.asyncio
async def test_linked_member_can_read_balances_and_settlements(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    balances = await balance_service.compute_balances(
        session, group.id, other_ws.id, other.id
    )
    assert balances is not None
    assert balances["lines"] == []

    settlements = await settlement_service.list_settlements(
        session, group.id, other_ws.id, other.id
    )
    assert settlements == []


@pytest.mark.asyncio
async def test_linked_member_can_settle_own_debt(
    session: AsyncSession, test_user, test_workspace
):
    """A linked member CAN record a settlement when they are the
    from_member (i.e., they're recording a payment they made)."""
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    self_m = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Me", is_self=True)
    )
    a = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="A", email="viewer@example.com"),
    )

    assert self_m is not None
    assert a is not None

    settlement = await settlement_service.create_settlement(
        session,
        group.id,
        other_ws.id,
        other.id,
        GroupSettlementCreate(
            from_member_id=a.id,  # the linked member themself
            to_member_id=self_m.id,
            amount=Decimal("5.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert settlement is not None
    assert settlement.from_member_id == a.id


@pytest.mark.asyncio
async def test_linked_member_cannot_settle_someone_elses_debt(
    session: AsyncSession, test_user, test_workspace
):
    """A linked member CANNOT speak for another member."""
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    a = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="A", email="viewer@example.com"),
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="B")
    )

    assert a is not None
    assert b is not None

    with pytest.raises(PermissionError):
        await settlement_service.create_settlement(
            session,
            group.id,
            other_ws.id,
            other.id,
            GroupSettlementCreate(
                from_member_id=b.id,  # NOT the linked member's own id
                to_member_id=a.id,
                amount=Decimal("5.00"),
                currency="USD",
                date=date.today(),
            ),
        )
