import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_defaults import (
    CATEGORY_TO_GROUP,
    DEFAULT_CATEGORIES,
    localized_name,
)
from app.services.category_group_service import create_default_groups


class CategoryVisibilityError(ValueError):
    """Raised when visibility is changed for a user-created category."""


async def create_default_categories(
    session: AsyncSession,
    user_id: uuid.UUID,
    lang: str = "pt-BR",
    workspace_id: Optional[uuid.UUID] = None,
) -> list[Category]:
    # Guard against double-creation. Scope the check to the workspace
    # when one is provided so a user creating a SECOND workspace still
    # gets the defaults seeded there — the prior guard checked
    # user_id and short-circuited every workspace after the first.
    if workspace_id is not None:
        existing = await session.execute(
            select(Category).where(Category.workspace_id == workspace_id).limit(1)
        )
        if existing.scalar_one_or_none():
            return await get_categories(session, workspace_id)
    else:
        # Legacy/test path with no explicit workspace_id — fall back to
        # the user's first workspace via the autostamp listener.
        existing = await session.execute(
            select(Category).where(Category.user_id == user_id).limit(1)
        )
        if existing.scalar_one_or_none():
            from app.models.workspace import Workspace, WorkspaceMember
            row = await session.execute(
                select(Workspace.id)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(WorkspaceMember.user_id == user_id)
                .limit(1)
            )
            scope_id = row.scalar()
            return await get_categories(session, scope_id) if scope_id else []

    # Create default groups first
    groups = await create_default_groups(session, user_id, lang, workspace_id=workspace_id)

    categories = []
    for key, data in DEFAULT_CATEGORIES.items():
        name = localized_name(data, lang)
        group_key = CATEGORY_TO_GROUP.get(key)
        group = groups.get(group_key) if group_key else None
        category = Category(
            user_id=user_id,
            workspace_id=workspace_id,
            name=name,
            icon=data["icon"],
            color=data["color"],
            is_system=True,
            group_id=group.id if group else None,
            treat_as_transfer=data.get("treat_as_transfer", False),
        )
        session.add(category)
        categories.append(category)
    await session.commit()
    return categories


async def get_hidden_category_ids(
    session: AsyncSession, workspace_id: uuid.UUID
) -> set[uuid.UUID]:
    """Ids of categories the workspace hides, directly or through their group.

    The rule engine needs these so it never files a transaction under a
    category the user has taken out of circulation.
    """
    result = await session.execute(
        select(Category.id)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(
            Category.workspace_id == workspace_id,
            or_(Category.is_hidden.is_(True), CategoryGroup.is_hidden.is_(True)),
        )
    )
    return set(result.scalars().all())


async def get_categories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    include_hidden: bool = False,
) -> list[Category]:
    filters = [Category.workspace_id == workspace_id]
    if not include_hidden:
        filters.append(Category.is_hidden.is_(False))
        filters.append(or_(Category.group_id.is_(None), CategoryGroup.is_hidden.is_(False)))

    result = await session.execute(
        select(Category)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(*filters)
        .order_by(Category.is_hidden.asc(), Category.is_system.desc(), Category.name)
    )
    return list(result.scalars().all())


async def get_category(
    session: AsyncSession, category_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Category]:
    result = await session.execute(
        select(Category).where(
            Category.id == category_id, Category.workspace_id == workspace_id
        )
    )
    return result.scalar_one_or_none()


async def create_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: CategoryCreate,
) -> Category:
    category = Category(user_id=user_id, workspace_id=workspace_id, **data.model_dump())
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def get_rules_assigning_category(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> list[Rule]:
    """Active rules whose `set_category` action targets this category.

    Hiding a category is how a user retires it, but a rule that assigns it
    keeps categorizing new transactions into it. The UI lists these so the
    user can retire the rules in the same step.
    """
    result = await session.execute(
        select(Rule)
        .where(Rule.workspace_id == workspace_id, Rule.is_active.is_(True))
        .order_by(Rule.priority, Rule.id)
    )
    target = str(category_id)
    return [
        rule
        for rule in result.scalars().all()
        if any(
            action.get("op") == "set_category" and str(action.get("value")) == target
            for action in (rule.actions or [])
        )
    ]


async def deactivate_rules_assigning_category(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> int:
    """Turn off the rules that assign this category and report how many."""
    rules = await get_rules_assigning_category(session, workspace_id, category_id)
    for rule in rules:
        rule.is_active = False
    return len(rules)


async def update_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: CategoryUpdate,
    *,
    deactivate_rules: bool = False,
) -> Optional[Category]:
    """Update a category, optionally retiring the rules that assign it.

    `deactivate_rules` only applies when the category is being hidden: the
    rules that filed transactions under it would otherwise stay listed as
    active while the engine skips their categorization.
    """
    category = await get_category(session, category_id, workspace_id)
    if not category:
        return None

    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_hidden") is True and not category.is_system:
        raise CategoryVisibilityError("Only system categories can be hidden")

    for key, value in changes.items():
        setattr(category, key, value)

    if deactivate_rules and changes.get("is_hidden") is True:
        await deactivate_rules_assigning_category(session, workspace_id, category_id)

    await session.commit()
    await session.refresh(category)
    return category


async def delete_category(
    session: AsyncSession, category_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    category = await get_category(session, category_id, workspace_id)
    if not category or category.is_system:
        return False

    try:
        await session.delete(category)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            "Category is still in use and cannot be deleted. Remove its references first."
        ) from exc
    return True
