import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, or_, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_category_allocation import TransactionCategoryAllocation
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_group_service import CATEGORY_TO_GROUP, create_default_groups


class CategoryVisibilityError(ValueError):
    """Raised when visibility is changed for a user-created category."""


class CategoryInUseError(ValueError):
    """Raised when a category with references is deleted without a destination."""


class CategoryTransferError(ValueError):
    """Raised when the destination given for a deletion cannot take the references."""


# Language-keyed translations for default categories
# Keys are internal identifiers used to map to groups and rules.
# `treat_as_transfer` marks categories whose transactions are flows, not
# income/expense — they're excluded from report aggregations like paired
# transfers are.
DEFAULT_CATEGORIES_I18N = {
    "housing":       {
        "en": "Housing",         "pt-BR": "Moradia",           "pt-PT": "Habitação",             "de": "Wohnen",             "fr": "Logement",                   "nl": "Wonen",                   "sk": "Bývanie", "icon": "house",            "color": "#8B5CF6"
    },
    "food":          {
        "en": "Food & Dining",   "pt-BR": "Alimentação",       "pt-PT": "Alimentação",           "de": "Essen & Trinken",    "fr": "Alimentation & Restaurants", "nl": "Eten & Drinken",          "sk": "Jedlo a reštaurácie", "icon": "utensils-crossed", "color": "#F59E0B"
    },
    "transport":     {
        "en": "Transport",       "pt-BR": "Transporte",        "pt-PT": "Transportes",           "de": "Transport",          "fr": "Transport",                  "nl": "Transport",               "sk": "Doprava", "icon": "car",              "color": "#3B82F6"
    },
    "groceries":     {
        "en": "Groceries",       "pt-BR": "Mercado",           "pt-PT": "Supermercado",          "de": "Lebensmittel",       "fr": "Courses",                    "nl": "Boodschappen",            "sk": "Potraviny", "icon": "shopping-cart",    "color": "#10B981"
    },
    "health":        {
        "en": "Health",          "pt-BR": "Saúde",             "pt-PT": "Saúde",                 "de": "Gesundheit",         "fr": "Santé",                      "nl": "Gezondheid",              "sk": "Zdravie", "icon": "pill",             "color": "#EF4444"
    },
    "leisure":       {
        "en": "Leisure",         "pt-BR": "Lazer",             "pt-PT": "Lazer",                 "de": "Freizeit",           "fr": "Loisirs",                    "nl": "Vrije tijd",              "sk": "Voľný čas", "icon": "gamepad-2",        "color": "#EC4899"
    },
    "subscriptions": {
        "en": "Subscriptions",   "pt-BR": "Assinaturas",       "pt-PT": "Subscrições",           "de": "Abonnements",        "fr": "Abonnements",                "nl": "Abonnementen",            "sk": "Predplatné", "icon": "smartphone",       "color": "#6366F1"
    },
    "education":     {
        "en": "Education",       "pt-BR": "Educação",          "pt-PT": "Educação",              "de": "Bildung",            "fr": "Éducation",                  "nl": "Educatie",                "sk": "Vzdelávanie", "icon": "book-open",        "color": "#22C55E"
    },
    "transfers":     {
        "en": "Transfers",       "pt-BR": "Transferências",    "pt-PT": "Transferências",        "de": "Umbuchungen",        "fr": "Virements",                  "nl": "Overboekingen",           "sk": "Prevody", "icon": "arrow-left-right", "color": "#64748B", "treat_as_transfer": True
    },
    "investments":   {
        "en": "Investments",     "pt-BR": "Investimentos",     "pt-PT": "Investimentos",         "de": "Investitionen",      "fr": "Investissements",            "nl": "Investeringen",           "sk": "Investície", "icon": "trending-up",      "color": "#0EA5E9", "treat_as_transfer": True
    },
    "salary":        {
        "en": "Salary & Income", "pt-BR": "Salário & Renda",   "pt-PT": "Salário & Rendimentos", "de": "Gehalt & Einnahmen", "fr": "Salaire & Revenus",          "nl": "Salaris & Inkomen",       "sk": "Mzda a príjmy", "icon": "banknote",         "color": "#16A34A", "is_income": True
    },
    "shopping":      {
        "en": "Shopping",        "pt-BR": "Compras",           "pt-PT": "Compras",               "de": "Shopping",           "fr": "Achats",                     "nl": "Winkelen",                "sk": "Nákupy", "icon": "shopping-bag",     "color": "#F97316"
    },
    "donations":     {
        "en": "Donations",       "pt-BR": "Doações",           "pt-PT": "Donativos",             "de": "Spenden",            "fr": "Dons",                       "nl": "Donaties",                "sk": "Dary", "icon": "heart-handshake",  "color": "#D946EF"
    },
    "personal_care": {
        "en": "Personal Care",   "pt-BR": "Cuidados Pessoais", "pt-PT": "Cuidados Pessoais",     "de": "Körperpflege",       "fr": "Soins personnels",           "nl": "Persoonlijke verzorging", "sk": "Osobná starostlivosť", "icon": "scissors",         "color": "#F472B6"
    },
    "taxes":         {
        "en": "Taxes & Fees",    "pt-BR": "Impostos & Taxas",  "pt-PT": "Impostos & Taxas",      "de": "Steuern & Gebühren", "fr": "Impôts & Taxes",             "nl": "Belastingen & Heffingen", "sk": "Dane a poplatky", "icon": "landmark",         "color": "#78716C"
    },
    "other":         {
        "en": "Other",           "pt-BR": "Outros",            "pt-PT": "Outros",                "de": "Sonstiges",          "fr": "Autres",                     "nl": "Overig",                  "sk": "Ostatné", "icon": "circle-help",      "color": "#6B7280"
    },
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
            is_income=data.get("is_income", None),
        )
        session.add(category)
        categories.append(category)
    await session.commit()
    return categories


async def get_assignable_category_ids(
    session: AsyncSession, workspace_id: uuid.UUID
) -> set[uuid.UUID]:
    """Ids the rule engine may file a transaction under.

    Categories the workspace hides, directly or through their group, are left
    out: hiding one is how a user takes it out of circulation. Asking which ids
    are usable rather than which are hidden also covers the id of a category
    that no longer exists, which a rule can still name because it keeps that id
    inside a JSON action with no foreign key behind it.
    """
    result = await session.execute(
        select(Category.id)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(
            Category.workspace_id == workspace_id,
            Category.is_hidden.is_(False),
            or_(Category.group_id.is_(None), CategoryGroup.is_hidden.is_(False)),
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    *,
    include_inactive: bool = False,
) -> list[Rule]:
    """Rules whose `set_category` action targets this category.

    Hiding a category is how a user retires it, but a rule that assigns it
    keeps categorizing new transactions into it. The UI lists these so the
    user can retire the rules in the same step.

    `include_inactive` is for deletion rather than hiding: a rule that is off
    today still has to be repointed, otherwise turning it back on later fails
    on a category that no longer exists.
    """
    filters = [Rule.workspace_id == workspace_id]
    if not include_inactive:
        filters.append(Rule.is_active.is_(True))
    result = await session.execute(
        select(Rule).where(*filters).order_by(Rule.priority, Rule.id)
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


@dataclass
class CategoryUsage:
    """Everything that still points at a category.

    Deleting one used to be a yes/no question. Now that a default category can
    go away too, the user has to be told what exactly is attached to it before
    choosing where it all moves.
    """

    transactions: int = 0
    budgets: int = 0
    recurring_transactions: int = 0
    category_allocations: int = 0
    rules: list[Rule] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.transactions
            or self.budgets
            or self.recurring_transactions
            or self.category_allocations
            or self.rules
        )


async def get_category_usage(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> CategoryUsage:
    """Count what references a category, so the delete dialog can name it."""
    counts: dict[str, int] = {}
    for key, model in (
        ("transactions", Transaction),
        ("budgets", Budget),
        ("recurring_transactions", RecurringTransaction),
    ):
        result = await session.execute(
            select(func.count())
            .select_from(model)
            .where(
                model.workspace_id == workspace_id,
                model.category_id == category_id,
            )
        )
        counts[key] = int(result.scalar_one())

    alloc_result = await session.execute(
        select(func.count())
        .select_from(TransactionCategoryAllocation)
        .where(
            TransactionCategoryAllocation.workspace_id == workspace_id,
            TransactionCategoryAllocation.category_id == category_id,
        )
    )
    counts["category_allocations"] = int(alloc_result.scalar_one())

    rules = await get_rules_assigning_category(
        session, workspace_id, category_id, include_inactive=True
    )
    return CategoryUsage(**counts, rules=rules)


async def _merge_budgets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Move the budgets over, adding up the ones that would land on each other.

    A budget is unique per user, category, month and kind, so moving one onto a
    month the destination already budgets would break that constraint. The two
    limits are added instead: they are the same person's ceiling for the same
    month, and dropping either would quietly lower it.
    """
    moving = (
        (
            await session.execute(
                select(Budget).where(
                    Budget.workspace_id == workspace_id,
                    Budget.category_id == category_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not moving:
        return

    existing = (
        (
            await session.execute(
                select(Budget).where(
                    Budget.workspace_id == workspace_id,
                    Budget.category_id == destination_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_slot = {(b.user_id, b.month, b.is_recurring): b for b in existing}

    for budget in moving:
        slot = (budget.user_id, budget.month, budget.is_recurring)
        target = by_slot.get(slot)
        if target is None:
            budget.category_id = destination_id
            by_slot[slot] = budget
            continue

        target.amount += budget.amount
        # The converted amount is only a sum when both sides carry one.
        if target.amount_primary is not None and budget.amount_primary is not None:
            target.amount_primary += budget.amount_primary
        else:
            target.amount_primary = None
        await session.delete(budget)


async def _repoint_rules(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Rewrite the `set_category` actions that name the category being deleted.

    A rule keeps the category id inside a JSON action with no foreign key
    behind it, so nothing at the database level notices it going stale. A rule
    left pointing at a deleted id fails the next time it files a transaction,
    and it takes the whole import or sync batch down with it.
    """
    rules = await get_rules_assigning_category(
        session, workspace_id, category_id, include_inactive=True
    )
    target = str(category_id)
    replacement = str(destination_id)
    for rule in rules:
        # Reassigned rather than edited in place: SQLAlchemy only notices a
        # JSON column changing when the attribute itself is set.
        rule.actions = [
            {**action, "value": replacement}
            if action.get("op") == "set_category" and str(action.get("value")) == target
            else action
            for action in (rule.actions or [])
        ]


async def _merge_category_allocations(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Transfer allocation rows from one category to another.

    If a transaction already has a destination row, sum amounts and delete the
    source row. Otherwise update category_id in place.
    """
    result = await session.execute(
        select(TransactionCategoryAllocation).where(
            TransactionCategoryAllocation.workspace_id == workspace_id,
            TransactionCategoryAllocation.category_id == category_id,
        )
    )
    source_rows = result.scalars().all()
    if not source_rows:
        return

    # Load destination rows that share a transaction_id with source rows
    source_tx_ids = [r.transaction_id for r in source_rows]
    dest_result = await session.execute(
        select(TransactionCategoryAllocation).where(
            TransactionCategoryAllocation.workspace_id == workspace_id,
            TransactionCategoryAllocation.category_id == destination_id,
            TransactionCategoryAllocation.transaction_id.in_(source_tx_ids),
        )
    )
    dest_by_tx: dict[uuid.UUID, TransactionCategoryAllocation] = {
        r.transaction_id: r for r in dest_result.scalars().all()
    }

    for src in source_rows:
        dest = dest_by_tx.get(src.transaction_id)
        if dest is not None:
            dest.amount += src.amount
            await session.delete(src)
        else:
            src.category_id = destination_id


async def _transfer_category_references(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Point everything that used one category at another, without committing."""
    for model in (Transaction, RecurringTransaction):
        await session.execute(
            sa_update(model)
            .where(
                model.workspace_id == workspace_id,
                model.category_id == category_id,
            )
            .values(category_id=destination_id)
        )
    await _merge_budgets(session, workspace_id, category_id, destination_id)
    await _merge_category_allocations(session, workspace_id, category_id, destination_id)
    await _repoint_rules(session, workspace_id, category_id, destination_id)


async def delete_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    transfer_to_id: Optional[uuid.UUID] = None,
) -> bool:
    """Delete a category, optionally moving what it holds to another one.

    Without a destination the deletion only goes through when nothing points at
    the category. With one, the transactions, budgets, recurring entries and
    rules are moved first, in the same transaction as the deletion, so the
    history survives and nothing is left pointing at an id that is gone.
    """
    category = await get_category(session, category_id, workspace_id)
    if not category:
        return False

    if transfer_to_id is not None:
        if transfer_to_id == category_id:
            raise CategoryTransferError("A category cannot be transferred into itself.")
        destination = await get_category(session, transfer_to_id, workspace_id)
        if not destination:
            raise CategoryTransferError("Destination category not found.")
        await _transfer_category_references(
            session, workspace_id, category_id, transfer_to_id
        )
    else:
        usage = await get_category_usage(session, workspace_id, category_id)
        if not usage.is_empty:
            raise CategoryInUseError(
                "Category is still in use and cannot be deleted. "
                "Choose another category to move its entries to."
            )

    try:
        await session.delete(category)
        await session.commit()
    except IntegrityError as exc:
        # Reached when something started using the category between the check
        # above and the delete.
        await session.rollback()
        raise CategoryInUseError(
            "Category is still in use and cannot be deleted. Remove its references first."
        ) from exc
    return True

