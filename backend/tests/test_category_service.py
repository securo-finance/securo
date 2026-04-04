import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
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
async def test_create_default_categories(session: AsyncSession, test_user):
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
async def test_create_default_categories_does_not_create_groups(session: AsyncSession, test_user):
    await create_default_categories(session, test_user.id, lang="pt-BR")

    result = await session.execute(
        select(Category).where(Category.user_id == test_user.id)
    )
    categories = result.scalars().all()
    assert len(categories) == len(DEFAULT_CATEGORIES_I18N)
    assert all(hasattr(cat, "name") for cat in categories)


@pytest.mark.asyncio
async def test_create_default_categories_english(session: AsyncSession, test_user):
    categories = await create_default_categories(session, test_user.id, lang="en")

    names = {c.name for c in categories}
    assert "Housing" in names
    assert "Food & Dining" in names
    assert "Transport" in names


@pytest.mark.asyncio
async def test_create_default_categories_race_guard(session: AsyncSession, test_user):
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
async def test_get_categories_ordered(session: AsyncSession, test_user):
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

    categories = await get_categories(session, test_user.id)
    system_cats = [c for c in categories if c.is_system]
    custom_cats = [c for c in categories if not c.is_system]

    if system_cats and custom_cats:
        system_idx = categories.index(system_cats[0])
        custom_idx = categories.index(custom_cats[0])
        assert system_idx < custom_idx


@pytest.mark.asyncio
async def test_get_categories_excludes_other_users(
    session: AsyncSession, test_user, test_categories
):
    categories = await get_categories(session, uuid.uuid4())
    assert len(categories) == 0


# ---------------------------------------------------------------------------
# get_category / create_category / update_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_category(session: AsyncSession, test_user):
    data = CategoryCreate(name="Pets", icon="paw-print", color="#8B4513")
    cat = await create_category(session, test_user.id, data)

    assert cat.name == "Pets"
    assert cat.icon == "paw-print"
    assert cat.is_system is False
    assert cat.has_budget is False
    assert cat.budget_amount is None


@pytest.mark.asyncio
async def test_get_category_by_id(session: AsyncSession, test_user):
    created = await create_category(
        session,
        test_user.id,
        CategoryCreate(name="Lookup", icon="search", color="#000000"),
    )
    fetched = await get_category(session, created.id, test_user.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_category_not_found(session: AsyncSession, test_user):
    result = await get_category(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_category(session: AsyncSession, test_user):
    cat = await create_category(
        session,
        test_user.id,
        CategoryCreate(name="OldName", icon="x", color="#111111"),
    )
    updated = await update_category(
        session,
        cat.id,
        test_user.id,
        CategoryUpdate(name="NewName", color="#222222"),
    )
    assert updated is not None
    assert updated.name == "NewName"
    assert updated.color == "#222222"
    assert updated.icon == "x"


@pytest.mark.asyncio
async def test_update_category_partial(session: AsyncSession, test_user, test_categories):
    cat = test_categories[1]
    original_icon = cat.icon
    original_color = cat.color
    updated = await update_category(
        session,
        cat.id,
        test_user.id,
        CategoryUpdate(name="Mobilidade"),
    )
    assert updated is not None
    assert updated.name == "Mobilidade"
    assert updated.icon == original_icon
    assert updated.color == original_color


@pytest.mark.asyncio
async def test_create_category_with_budget_sets_budget_state(session: AsyncSession, test_user):
    cat = await create_category(
        session,
        test_user.id,
        CategoryCreate(name="Budgeted", icon="wallet", color="#123456", budget_amount=123),
    )

    assert cat.has_budget is True
    assert cat.budget_amount == 123


@pytest.mark.asyncio
async def test_update_category_disables_budget_when_has_budget_false(
    session: AsyncSession, test_user, test_categories
):
    cat = test_categories[0]
    cat.has_budget = True
    cat.budget_amount = 456
    await session.commit()

    updated = await update_category(
        session,
        cat.id,
        test_user.id,
        CategoryUpdate(has_budget=False),
    )

    assert updated is not None
    assert updated.has_budget is False
    assert updated.budget_amount is None


@pytest.mark.asyncio
async def test_update_category_not_found(session: AsyncSession, test_user):
    result = await update_category(
        session,
        uuid.uuid4(),
        test_user.id,
        CategoryUpdate(name="Nope"),
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_custom_category(session: AsyncSession, test_user):
    cat = await create_category(
        session,
        test_user.id,
        CategoryCreate(name="ToDelete", icon="trash", color="#FF0000"),
    )
    assert await delete_category(session, cat.id, test_user.id) is True
    assert await get_category(session, cat.id, test_user.id) is None


@pytest.mark.asyncio
async def test_delete_system_category_rejected(session: AsyncSession, test_user):
    categories = await create_default_categories(session, test_user.id)
    system_cat = categories[0]

    assert system_cat.is_system is True
    assert await delete_category(session, system_cat.id, test_user.id) is False
    assert await get_category(session, system_cat.id, test_user.id) is not None


@pytest.mark.asyncio
async def test_delete_category_not_found(session: AsyncSession, test_user):
    assert await delete_category(session, uuid.uuid4(), test_user.id) is False
