import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.schemas.category_group import CategoryGroupCreate, CategoryGroupUpdate
from app.services.category_group_service import (
    DEFAULT_GROUPS_I18N,
    create_default_groups,
    create_group,
    delete_group,
    get_group,
    get_groups,
    update_group,
)


# ---------------------------------------------------------------------------
# create_default_groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_default_groups(session: AsyncSession, test_user, test_workspace):
    groups = await create_default_groups(session, test_user.id, lang="pt-BR")
    await session.commit()

    assert len(groups) == len(DEFAULT_GROUPS_I18N)
    assert "housing" in groups
    assert "food" in groups
    assert "income" in groups

    # Check localized names
    assert groups["housing"].name == "Moradia"
    assert groups["food"].name == "Alimentação"
    assert groups["income"].name == "Renda"

    # All should be system groups
    for g in groups.values():
        assert g.is_system is True


@pytest.mark.asyncio
async def test_create_default_groups_english(session: AsyncSession, test_user, test_workspace):
    groups = await create_default_groups(session, test_user.id, lang="en")
    await session.commit()

    assert groups["housing"].name == "Housing"
    assert groups["food"].name == "Food & Dining"


@pytest.mark.asyncio
async def test_create_default_groups_russian(session: AsyncSession, test_user, test_workspace):
    groups = await create_default_groups(session, test_user.id, lang="ru")
    await session.commit()

    assert groups["housing"].name == "Жильё"
    assert groups["food"].name == "Еда и рестораны"
    assert groups["income"].name == "Доходы"


# ---------------------------------------------------------------------------
# get_groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_groups_ordered_by_position(session: AsyncSession, test_user, test_workspace):
    await create_default_groups(session, test_user.id)
    await session.commit()

    groups = await get_groups(session, test_workspace.id)
    assert len(groups) == len(DEFAULT_GROUPS_I18N)

    positions = [g.position for g in groups]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_get_groups_loads_categories(session: AsyncSession, test_user, test_workspace):
    groups_map = await create_default_groups(session, test_user.id)
    await session.commit()

    # Add a category to one group
    cat = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="TestCat",
        icon="star",
        color="#FF0000",
        is_system=False,
        group_id=groups_map["food"].id,
    )
    session.add(cat)
    await session.commit()

    groups = await get_groups(session, test_workspace.id)
    food_group = [g for g in groups if g.name == "Alimentação"][0]
    assert len(food_group.categories) >= 1


# ---------------------------------------------------------------------------
# get_group / create_group / update_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_group(session: AsyncSession, test_user, test_workspace):
    data = CategoryGroupCreate(name="Pets", icon="paw-print", color="#FF5733", position=10)
    group = await create_group(session, test_workspace.id, test_user.id, data)

    assert group.name == "Pets"
    assert group.icon == "paw-print"
    assert group.is_system is False


@pytest.mark.asyncio
async def test_get_group_by_id(session: AsyncSession, test_user, test_workspace):
    created = await create_group(
        session,
        test_workspace.id, test_user.id,
        CategoryGroupCreate(name="Lookup", icon="search", color="#000000"),
    )
    fetched = await get_group(session, created.id, test_workspace.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_group_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_group(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_group(session: AsyncSession, test_user, test_workspace):
    group = await create_group(
        session,
        test_workspace.id, test_user.id,
        CategoryGroupCreate(name="Old", icon="x", color="#111111"),
    )
    updated = await update_group(
        session,
        group.id,
        test_workspace.id,
        CategoryGroupUpdate(name="New", color="#222222"),
    )
    assert updated is not None
    assert updated.name == "New"
    assert updated.color == "#222222"
    assert updated.icon == "x"  # unchanged


@pytest.mark.asyncio
async def test_update_group_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_group(
        session,
        uuid.uuid4(),
        test_workspace.id,
        CategoryGroupUpdate(name="Nope"),
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_custom_group(session: AsyncSession, test_user, test_workspace):
    group = await create_group(
        session,
        test_workspace.id, test_user.id,
        CategoryGroupCreate(name="ToDelete", icon="trash", color="#FF0000"),
    )
    assert await delete_group(session, group.id, test_workspace.id) is True
    assert await get_group(session, group.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_system_group_rejected(session: AsyncSession, test_user, test_workspace):
    groups = await create_default_groups(session, test_user.id)
    await session.commit()

    # Try deleting a system group
    system_group = groups["housing"]
    assert await delete_group(session, system_group.id, test_workspace.id) is False
    # Should still exist
    assert await get_group(session, system_group.id, test_workspace.id) is not None


@pytest.mark.asyncio
async def test_delete_group_unlinks_categories(session: AsyncSession, test_user, test_workspace):
    group = await create_group(
        session,
        test_workspace.id, test_user.id,
        CategoryGroupCreate(name="UnlinkMe", icon="link", color="#00FF00"),
    )
    cat = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="ChildCat",
        icon="star",
        color="#0000FF",
        is_system=False,
        group_id=group.id,
    )
    session.add(cat)
    await session.commit()

    assert await delete_group(session, group.id, test_workspace.id) is True

    # Category should still exist but group_id should be None
    result = await session.execute(select(Category).where(Category.id == cat.id))
    orphan = result.scalar_one_or_none()
    assert orphan is not None
    assert orphan.group_id is None


@pytest.mark.asyncio
async def test_delete_group_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_group(session, uuid.uuid4(), test_workspace.id) is False
