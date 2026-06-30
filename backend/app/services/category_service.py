import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_group_service import CATEGORY_TO_GROUP, create_default_groups


# Language-keyed translations for default categories
# Keys are internal identifiers used to map to groups and rules.
# `treat_as_transfer` marks categories whose transactions are flows, not
# income/expense — they're excluded from report aggregations like paired
# transfers are.
DEFAULT_CATEGORIES_I18N = {
    "housing":       {"en": "Housing",       "pt-BR": "Moradia",        "de": "Wohnen",            "icon": "house",            "color": "#8B5CF6"},
    "food":          {"en": "Food & Dining", "pt-BR": "Alimentação",    "de": "Essen & Trinken",   "icon": "utensils-crossed", "color": "#F59E0B"},
    "transport":     {"en": "Transport",     "pt-BR": "Transporte",     "de": "Transport",         "icon": "car",              "color": "#3B82F6"},
    "groceries":     {"en": "Groceries",     "pt-BR": "Mercado",        "de": "Lebensmittel",      "icon": "shopping-cart",    "color": "#10B981"},
    "health":        {"en": "Health",        "pt-BR": "Saúde",          "de": "Gesundheit",        "icon": "pill",             "color": "#EF4444"},
    "leisure":       {"en": "Leisure",       "pt-BR": "Lazer",          "de": "Freizeit",          "icon": "gamepad-2",        "color": "#EC4899"},
    "subscriptions": {"en": "Subscriptions", "pt-BR": "Assinaturas",    "de": "Abonnements",       "icon": "smartphone",       "color": "#6366F1"},
    "education":     {"en": "Education",     "pt-BR": "Educação",       "de": "Bildung",           "icon": "book-open",        "color": "#22C55E"},
    "transfers":     {"en": "Transfers",     "pt-BR": "Transferências", "de": "Umbuchungen",       "icon": "arrow-left-right", "color": "#64748B", "treat_as_transfer": True},
    "investments":   {"en": "Investments",   "pt-BR": "Investimentos",  "de": "Investitionen",     "icon": "trending-up",      "color": "#0EA5E9", "treat_as_transfer": True},
    "salary":        {"en": "Salary & Income",  "pt-BR": "Salário & Renda",     "de": "Gehalt & Einnahmen", "icon": "banknote",         "color": "#16A34A"},
    "shopping":      {"en": "Shopping",         "pt-BR": "Compras",             "de": "Shopping",           "icon": "shopping-bag",     "color": "#F97316"},
    "donations":     {"en": "Donations",        "pt-BR": "Doações",             "de": "Spenden",            "icon": "heart-handshake",  "color": "#D946EF"},
    "personal_care": {"en": "Personal Care",    "pt-BR": "Cuidados Pessoais",   "de": "Körperpflege",       "icon": "scissors",         "color": "#F472B6"},
    "taxes":         {"en": "Taxes & Fees",     "pt-BR": "Impostos & Taxas",    "de": "Steuern & Gebühren", "icon": "landmark",         "color": "#78716C"},
    "other":         {"en": "Other",         "pt-BR": "Outros",         "de": "Sonstiges",          "icon": "circle-help",      "color": "#6B7280"},
}


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
    for key, data in DEFAULT_CATEGORIES_I18N.items():
        name = data.get(lang, data.get("en", key))
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


async def get_categories(session: AsyncSession, workspace_id: uuid.UUID) -> list[Category]:
    result = await session.execute(
        select(Category)
        .where(Category.workspace_id == workspace_id)
        .order_by(Category.is_system.desc(), Category.name)
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


async def update_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: CategoryUpdate,
) -> Optional[Category]:
    category = await get_category(session, category_id, workspace_id)
    if not category:
        return None

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(category, key, value)

    await session.commit()
    await session.refresh(category)
    return category


async def delete_category(
    session: AsyncSession, category_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    category = await get_category(session, category_id, workspace_id)
    if not category or category.is_system:
        return False

    await session.delete(category)
    await session.commit()
    return True
