import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.group import (
    GroupCreate,
    GroupMemberCreate,
    GroupMemberUpdate,
    GroupUpdate,
)
from app.services import group_service


@pytest.mark.asyncio
async def test_create_group_defaults(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Roommates")
    )
    assert group.name == "Roommates"
    assert group.kind == "social"
    assert group.default_currency == "USD"
    assert group.is_archived is False
    assert group.user_id == test_user.id


@pytest.mark.asyncio
async def test_create_group_b2b_kind(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session,
        test_workspace.id,
        test_user.id,
        GroupCreate(name="Marketing", kind="cost_center", default_currency="EUR"),
    )
    assert group.kind == "cost_center"
    assert group.default_currency == "EUR"


@pytest.mark.asyncio
async def test_duplicate_group_name_rejected(
    session: AsyncSession, test_user, test_workspace
):
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    with pytest.raises(ValueError, match="already exists"):
        await group_service.create_group(
            session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
        )


@pytest.mark.asyncio
async def test_list_groups_excludes_archived_by_default(
    session: AsyncSession, test_user, test_workspace
):
    g1 = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="A")
    )
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="B")
    )
    await group_service.update_group(
        session, g1.id, test_workspace.id, test_user.id, GroupUpdate(is_archived=True)
    )

    visible = await group_service.list_groups(session, test_workspace.id, test_user.id)
    assert {g.name for g in visible} == {"B"}

    all_groups = await group_service.list_groups(
        session, test_workspace.id, test_user.id, include_archived=True
    )
    assert {g.name for g in all_groups} == {"A", "B"}


@pytest.mark.asyncio
async def test_get_group_scoped_to_owner(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="X")
    )
    # A bogus workspace must not be able to see the group via get_group.
    other_workspace = uuid.uuid4()
    fetched = await group_service.get_group(session, group.id, other_workspace)
    assert fetched is None


@pytest.mark.asyncio
async def test_create_member_and_unique_name(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    member = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Alice", is_self=True),
    )

    assert member is not None
    assert member.name == "Alice"
    assert member.is_self is True

    with pytest.raises(ValueError, match="already exists"):
        await group_service.create_member(
            session, group.id, test_workspace.id, GroupMemberCreate(name="Alice")
        )


@pytest.mark.asyncio
async def test_only_one_self_member_allowed(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    m1 = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Me", is_self=True)
    )

    assert m1 is not None
    m2 = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Also Me", is_self=True),
    )

    assert m2 is not None
    # m1 should have been demoted automatically.
    refreshed = await group_service.list_members(
        session, group.id, test_workspace.id, test_user.id
    )

    assert refreshed is not None
    by_id = {m.id: m for m in refreshed}
    assert by_id[m1.id].is_self is False
    assert by_id[m2.id].is_self is True


@pytest.mark.asyncio
async def test_update_member_promotes_to_self_demotes_others(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    m1 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A", is_self=True)
    )
    assert m1 is not None
    m2 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B")
    )

    assert m2 is not None

    await group_service.update_member(
        session,
        group.id,
        m2.id,
        test_workspace.id,
        GroupMemberUpdate(is_self=True),
    )
    refreshed = await group_service.list_members(
        session, group.id, test_workspace.id, test_user.id
    )

    assert refreshed is not None
    by_id = {m.id: m for m in refreshed}
    assert by_id[m1.id].is_self is False
    assert by_id[m2.id].is_self is True


@pytest.mark.asyncio
async def test_member_operations_isolated_per_owner(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    other_workspace = uuid.uuid4()
    result = await group_service.create_member(
        session, group.id, other_workspace, GroupMemberCreate(name="Bob")
    )
    assert result is None  # group not visible from a different workspace
