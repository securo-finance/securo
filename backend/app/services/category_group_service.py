import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.schemas.category_group import CategoryGroupCreate, CategoryGroupUpdate


class CategoryGroupVisibilityError(ValueError):
    """Raised when visibility is changed for a user-created category group."""


# Language-keyed translations for default groups
# Keys are internal identifiers, values are {lang: display_name}
DEFAULT_GROUPS_I18N = {
    "housing":   {"ru": "Жильё", "uk": "Житло", "de": "Wohnen", "fr": "Logement", "it": "Casa", "pl": "Mieszkanie", "es": "Alojamiento", "en": "Housing",       "pt-BR": "Moradia",         "pt-PT": "Habitação",       "icon": "house",            "color": "#8B5CF6", "position": 0},
    "food":      {"ru": "Еда и рестораны", "uk": "Їжа та ресторани", "de": "Essen & Trinken", "fr": "Alimentation & Restaurants", "it": "Cibo e Ristoranti", "pl": "Jedzenie", "es": "Comida y Cena", "en": "Food & Dining", "pt-BR": "Alimentação",     "pt-PT": "Alimentação",     "icon": "utensils-crossed", "color": "#F59E0B", "position": 1},
    "transport":  {"ru": "Транспорт", "uk": "Транспорт", "de": "Transport", "fr": "Transport", "it": "Trasporti", "pl": "Transport", "es": "Transporte",     "en": "Transport",     "pt-BR": "Transporte",      "pt-PT": "Transportes",     "icon": "car",              "color": "#3B82F6", "position": 2},
    "lifestyle": {"ru": "Образ жизни", "uk": "Спосіб життя", "de": "Lifestyle", "fr": "Style de vie", "it": "Stile di Vita", "pl": "Styl życia", "es": "Estilo de Vida", "en": "Lifestyle",     "pt-BR": "Estilo de Vida",  "pt-PT": "Estilo de Vida",  "icon": "sparkles",         "color": "#EC4899", "position": 3},
    "income":    {"ru": "Доходы", "uk": "Доходи", "de": "Einkommen", "fr": "Revenus", "it": "Entrate", "pl": "Przychody", "es": "Ingresos",        "en": "Income",        "pt-BR": "Renda",           "pt-PT": "Rendimentos",     "icon": "trending-up",      "color": "#16A34A", "position": 5},
    "other":     {"ru": "Другое", "uk": "Інше", "de": "Sonstiges", "fr": "Autres", "it": "Altro", "pl": "Inne", "es": "Otros",            "en": "Other",         "pt-BR": "Outros",          "pt-PT": "Outros",          "icon": "circle-help",      "color": "#64748B", "position": 4},
}

# Maps category internal key -> group internal key
CATEGORY_TO_GROUP = {
    "housing": "housing",
    "food": "food",
    "groceries": "food",
    "transport": "transport",
    "health": "lifestyle",
    "leisure": "lifestyle",
    "education": "lifestyle",
    "subscriptions": "other",
    "salary": "income",
    "shopping": "other",
    "donations": "other",
    "personal_care": "lifestyle",
    "taxes": "other",
    "transfers": "other",
    "investments": "other",
    "other": "other",
}


def _resolve_group_name(key: str, lang: str) -> str:
    entry = DEFAULT_GROUPS_I18N.get(key, {})
    return str(entry.get(lang, entry.get("en", key)))


async def create_default_groups(
    session: AsyncSession,
    user_id: uuid.UUID,
    lang: str = "pt-BR",
    workspace_id: Optional[uuid.UUID] = None,
) -> dict[str, CategoryGroup]:
    """Create default category groups for a user. Returns dict of internal_key -> group. Uses flush (not commit)."""
    groups = {}
    for key, data in DEFAULT_GROUPS_I18N.items():
        name = data.get(lang, data.get("en", key))
        group = CategoryGroup(
            user_id=user_id,
            workspace_id=workspace_id,
            name=name,
            icon=data["icon"],
            color=data["color"],
            position=data["position"],
            is_system=True,
        )
        session.add(group)
        groups[key] = group
    await session.flush()
    return groups


async def get_groups(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    include_hidden: bool = False,
) -> list[CategoryGroup]:
    filters = [CategoryGroup.workspace_id == workspace_id]
    if not include_hidden:
        filters.append(CategoryGroup.is_hidden.is_(False))

    category_loader = selectinload(CategoryGroup.categories)
    if not include_hidden:
        category_loader = selectinload(CategoryGroup.categories.and_(Category.is_hidden.is_(False)))

    result = await session.execute(
        select(CategoryGroup)
        .where(*filters)
        .options(category_loader)
        .order_by(CategoryGroup.is_hidden.asc(), CategoryGroup.position)
    )
    return list(result.scalars().all())


async def get_group(session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID) -> Optional[CategoryGroup]:
    result = await session.execute(
        select(CategoryGroup)
        .where(CategoryGroup.id == group_id, CategoryGroup.workspace_id == workspace_id)
        .options(selectinload(CategoryGroup.categories))
    )
    return result.scalar_one_or_none()


async def create_group(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: CategoryGroupCreate,
) -> CategoryGroup:
    group = CategoryGroup(user_id=user_id, workspace_id=workspace_id, **data.model_dump())
    session.add(group)
    await session.commit()
    created = await get_group(session, group.id, workspace_id)
    if created is None:
        raise RuntimeError("Failed to reload created category group")
    return created


async def update_group(
    session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID, data: CategoryGroupUpdate
) -> Optional[CategoryGroup]:
    group = await get_group(session, group_id, workspace_id)
    if not group:
        return None

    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_hidden") is True and not group.is_system:
        raise CategoryGroupVisibilityError("Only system category groups can be hidden")

    for key, value in changes.items():
        setattr(group, key, value)

    await session.commit()
    return await get_group(session, group_id, workspace_id)


async def delete_group(session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    group = await get_group(session, group_id, workspace_id)
    if not group or group.is_system:
        return False

    # Unlink children before deleting
    await session.execute(
        update(Category).where(Category.group_id == group_id).values(group_id=None)
    )

    await session.delete(group)
    await session.commit()
    return True
