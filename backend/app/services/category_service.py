import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_group_service import CATEGORY_TO_GROUP, create_default_groups


# Language-keyed translations for default categories
# Keys are internal identifiers used to map to groups and rules.
# `treat_as_transfer` marks categories whose transactions are flows, not
# income/expense — they're excluded from report aggregations like paired
# transfers are.
DEFAULT_CATEGORIES_I18N = {
    "housing":       {"en": "Housing",       "pt-BR": "Moradia",        "icon": "house",            "color": "#8B5CF6"},
    "food":          {"en": "Food & Dining", "pt-BR": "Alimentação",    "icon": "utensils-crossed", "color": "#F59E0B"},
    "transport":     {"en": "Transport",     "pt-BR": "Transporte",     "icon": "car",              "color": "#3B82F6"},
    "groceries":     {"en": "Groceries",     "pt-BR": "Mercado",        "icon": "shopping-cart",    "color": "#10B981"},
    "health":        {"en": "Health",        "pt-BR": "Saúde",          "icon": "pill",             "color": "#EF4444"},
    "leisure":       {"en": "Leisure",       "pt-BR": "Lazer",          "icon": "gamepad-2",        "color": "#EC4899"},
    "subscriptions": {"en": "Subscriptions", "pt-BR": "Assinaturas",    "icon": "smartphone",       "color": "#6366F1"},
    "education":     {"en": "Education",     "pt-BR": "Educação",       "icon": "book-open",        "color": "#22C55E"},
    "transfers":     {"en": "Transfers",     "pt-BR": "Transferências", "icon": "arrow-left-right", "color": "#64748B", "treat_as_transfer": True},
    "investments":   {"en": "Investments",   "pt-BR": "Investimentos",  "icon": "trending-up",      "color": "#0EA5E9", "treat_as_transfer": True},
    "salary":        {"en": "Salary & Income",  "pt-BR": "Salário & Renda",     "icon": "banknote",         "color": "#16A34A"},
    "shopping":      {"en": "Shopping",         "pt-BR": "Compras",             "icon": "shopping-bag",     "color": "#F97316"},
    "donations":     {"en": "Donations",        "pt-BR": "Doações",             "icon": "heart-handshake",  "color": "#D946EF"},
    "personal_care": {"en": "Personal Care",    "pt-BR": "Cuidados Pessoais",   "icon": "scissors",         "color": "#F472B6"},
    "taxes":         {"en": "Taxes & Fees",     "pt-BR": "Impostos & Taxas",    "icon": "landmark",         "color": "#78716C"},
    "other":         {"en": "Other",         "pt-BR": "Outros",         "icon": "circle-help",      "color": "#6B7280"},
}

INCOME_CATEGORY_KEYS = {"salary"}


def flow_type_for_category_key(key: str) -> str:
    return "income" if key in INCOME_CATEGORY_KEYS else "expense"


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
            flow_type=flow_type_for_category_key(key),
            is_system=True,
            group_id=group.id if group else None,
            treat_as_transfer=data.get("treat_as_transfer", False),
        )
        session.add(category)
        categories.append(category)
    await session.commit()
    return categories


async def get_categories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    flow_type: Optional[str] = None,
) -> list[Category]:
    query = select(Category).where(Category.workspace_id == workspace_id)
    if flow_type:
        query = query.where(Category.flow_type == flow_type)
    result = await session.execute(
        query.order_by(Category.flow_type, Category.is_system.desc(), Category.name)
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


async def _validate_group_for_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    group_id: Optional[uuid.UUID],
    flow_type: str,
) -> None:
    if group_id is None:
        return
    group = await session.get(CategoryGroup, group_id)
    if group is None or group.workspace_id != workspace_id:
        raise ValueError("Category group not found")
    if group.flow_type != flow_type:
        raise ValueError("Category flow type must match group flow type")


def category_flow_for_transaction_type(transaction_type: str) -> str:
    return "income" if transaction_type == "credit" else "expense"


async def validate_category_for_transaction(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: Optional[uuid.UUID],
    transaction_type: str,
) -> None:
    if category_id is None:
        return
    category = await get_category(session, category_id, workspace_id)
    if category is None:
        raise ValueError("Category not found")
    expected_flow = category_flow_for_transaction_type(transaction_type)
    if category.flow_type != expected_flow:
        raise ValueError(
            f"Category flow type '{category.flow_type}' is not valid for "
            f"{transaction_type} transactions"
        )


async def create_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: CategoryCreate,
) -> Category:
    payload = data.model_dump()
    await _validate_group_for_category(
        session, workspace_id, payload.get("group_id"), payload["flow_type"]
    )
    category = Category(user_id=user_id, workspace_id=workspace_id, **payload)
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

    update_data = data.model_dump(exclude_unset=True)
    next_flow_type = update_data.get("flow_type", category.flow_type)
    next_group_id = update_data.get("group_id", category.group_id)
    await _validate_group_for_category(session, workspace_id, next_group_id, next_flow_type)

    for key, value in update_data.items():
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
