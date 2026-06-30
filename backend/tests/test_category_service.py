import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_service import (
    DEFAULT_CATEGORIES_I18N,
    create_category,
    create_default_categories,
    delete_category,
    get_categories,
    get_category,
    update_category,
)


# ---------------------------------------------------------------------------
# create_default_categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_default_categories(session: AsyncSession, test_user, test_workspace):
    categories = await create_default_categories(session, test_user.id, lang="pt-BR")

    assert len(categories) == len(DEFAULT_CATEGORIES_I18N)

    names = {c.name for c in categories}
    assert "Moradia" in names
    assert "Alimentação" in names
    assert "Transporte" in names
    assert "Outros" in names

    for cat in categories:
        assert cat.is_system is True


@pytest.mark.asyncio
async def test_create_default_categories_german(session: AsyncSession, test_user, test_workspace):
    categories = await create_default_categories(session, test_user.id, lang="de")

    assert len(categories) == len(DEFAULT_CATEGORIES_I18N)

    names = {c.name for c in categories}
    assert "Wohnen" in names
    assert "Essen & Trinken" in names
    assert "Lebensmittel" in names
    assert "Sonstiges" in names

    for cat in categories:
        assert cat.is_system is True


@pytest.mark.asyncio
async def test_create_default_categories_creates_groups(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")

    result = await session.execute(
        select(CategoryGroup).where(CategoryGroup.user_id == test_user.id)
    )
    groups = result.scalars().all()
    assert len(groups) == 6  # housing, food, transport, lifestyle, income, other


@pytest.mark.asyncio
async def test_create_default_categories_links_to_groups(session: AsyncSession, test_user, test_workspace):
    categories = await create_default_categories(session, test_user.id, lang="pt-BR")

    with_group = [c for c in categories if c.group_id is not None]
    assert len(with_group) == len(DEFAULT_CATEGORIES_I18N)


@pytest.mark.asyncio
async def test_create_default_categories_english(session: AsyncSession, test_user, test_workspace):
    categories = await create_default_categories(session, test_user.id, lang="en")

    names = {c.name for c in categories}
    assert "Housing" in names
    assert "Food & Dining" in names
    assert "Transport" in names


@pytest.mark.asyncio
async def test_create_default_categories_race_guard(session: AsyncSession, test_user, test_workspace):
    """Second call should return existing instead of duplicating."""
    first = await create_default_categories(session, test_user.id, lang="pt-BR")
    second = await create_default_categories(session, test_user.id, lang="pt-BR")

    assert len(second) == len(first)
    first_ids = {c.id for c in first}
    second_ids = {c.id for c in second}
    assert first_ids == second_ids


# ---------------------------------------------------------------------------
# get_categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_categories_ordered(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id)

    custom = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="AAA Custom",
        icon="star",
        color="#FF0000",
        is_system=False,
    )
    session.add(custom)
    await session.commit()

    categories = await get_categories(session, test_workspace.id)
    system_cats = [c for c in categories if c.is_system]
    custom_cats = [c for c in categories if not c.is_system]

    if system_cats and custom_cats:
        system_idx = categories.index(system_cats[0])
        custom_idx = categories.index(custom_cats[0])
        assert system_idx < custom_idx


@pytest.mark.asyncio
async def test_get_categories_excludes_other_users(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    categories = await get_categories(session, uuid.uuid4())
    assert len(categories) == 0


# ---------------------------------------------------------------------------
# get_category / create_category / update_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_category(session: AsyncSession, test_user, test_workspace):
    data = CategoryCreate(name="Pets", icon="paw-print", color="#8B4513")
    cat = await create_category(session, test_workspace.id, test_user.id, data)

    assert cat.name == "Pets"
    assert cat.icon == "paw-print"
    assert cat.is_system is False


@pytest.mark.asyncio
async def test_create_category_with_group(session: AsyncSession, test_user, test_workspace):
    from app.services.category_group_service import create_group
    from app.schemas.category_group import CategoryGroupCreate

    group = await create_group(
        session,
        test_workspace.id, test_user.id,
        CategoryGroupCreate(name="CustomGroup", icon="folder", color="#000"),
    )
    cat = await create_category(
        session,
        test_workspace.id, test_user.id,
        CategoryCreate(name="WithGroup", icon="star", color="#FFF", group_id=group.id),
    )
    assert cat.group_id == group.id


@pytest.mark.asyncio
async def test_get_category_by_id(session: AsyncSession, test_user, test_workspace):
    created = await create_category(
        session,
        test_workspace.id, test_user.id,
        CategoryCreate(name="Lookup", icon="search", color="#000000"),
    )
    fetched = await get_category(session, created.id, test_workspace.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_category_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_category(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_category(session: AsyncSession, test_user, test_workspace):
    cat = await create_category(
        session,
        test_workspace.id, test_user.id,
        CategoryCreate(name="OldName", icon="x", color="#111111"),
    )
    updated = await update_category(
        session,
        cat.id,
        test_workspace.id,
        CategoryUpdate(name="NewName", color="#222222"),
    )
    assert updated is not None
    assert updated.name == "NewName"
    assert updated.color == "#222222"
    assert updated.icon == "x"


@pytest.mark.asyncio
async def test_update_category_partial(session: AsyncSession, test_user, test_workspace, test_categories):
    cat = test_categories[1]
    original_icon = cat.icon
    original_color = cat.color
    updated = await update_category(
        session,
        cat.id,
        test_workspace.id,
        CategoryUpdate(name="Mobilidade"),
    )
    assert updated is not None
    assert updated.name == "Mobilidade"
    assert updated.icon == original_icon
    assert updated.color == original_color


@pytest.mark.asyncio
async def test_update_category_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_category(
        session,
        uuid.uuid4(),
        test_workspace.id,
        CategoryUpdate(name="Nope"),
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_custom_category(session: AsyncSession, test_user, test_workspace):
    cat = await create_category(
        session,
        test_workspace.id, test_user.id,
        CategoryCreate(name="ToDelete", icon="trash", color="#FF0000"),
    )
    assert await delete_category(session, cat.id, test_workspace.id) is True
    assert await get_category(session, cat.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_system_category_rejected(session: AsyncSession, test_user, test_workspace):
    categories = await create_default_categories(session, test_user.id)
    system_cat = categories[0]

    assert system_cat.is_system is True
    assert await delete_category(session, system_cat.id, test_workspace.id) is False
    assert await get_category(session, system_cat.id, test_workspace.id) is not None


@pytest.mark.asyncio
async def test_delete_category_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_category(session, uuid.uuid4(), test_workspace.id) is False
