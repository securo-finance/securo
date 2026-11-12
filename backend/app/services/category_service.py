import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import groupby
from typing import Optional

from sqlalchemy import func, or_, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.recurring_transaction_category_line import RecurringTransactionCategoryLine
from app.models.transaction_category_line import TransactionCategoryLine
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.category_defaults import (
    CATEGORY_TO_GROUP,
    DEFAULT_CATEGORIES,
    localized_name,
)
from app.services.category_group_service import create_default_groups


class CategoryVisibilityError(ValueError):
    """Raised when visibility is changed for a user-created category."""


class CategoryInUseError(ValueError):
    """Raised when a category with references is deleted without a destination."""


class CategoryTransferError(ValueError):
    """Raised when the destination given for a deletion cannot take the references."""


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
    if (
        (changes.get("is_hidden") is True and not category.is_hidden)
        or (changes.get("is_ignored") is True and not category.is_ignored)
    ) and await _used_in_category_lines(session, workspace_id, category_id):
        # An ignored category takes a row out of the balance, and a split row
        # stays in the balance as a whole; a hidden one cannot be picked.
        raise CategoryVisibilityError(
            "This category is used in transactions split across categories. "
            "Change those splits first."
        )

    if (
        changes.get("treat_as_transfer") is True
        and not category.treat_as_transfer
        and await _used_in_card_credit_lines(session, workspace_id, category_id)
    ):
        raise CategoryVisibilityError(_CARD_CREDIT_TRANSFER)

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
    rules: list[Rule] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.transactions
            or self.budgets
            or self.recurring_transactions
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

    # A transaction split across categories references them through its
    # lines; count each such transaction once.
    split_count = await session.scalar(
        select(func.count(func.distinct(TransactionCategoryLine.transaction_id))).where(
            TransactionCategoryLine.workspace_id == workspace_id,
            TransactionCategoryLine.category_id == category_id,
        )
    )
    counts["transactions"] += int(split_count or 0)
    # Likewise a recurring bill split across categories.
    split_recurring = await session.scalar(
        select(
            func.count(func.distinct(RecurringTransactionCategoryLine.recurring_transaction_id))
        ).where(
            RecurringTransactionCategoryLine.workspace_id == workspace_id,
            RecurringTransactionCategoryLine.category_id == category_id,
        )
    )
    counts["recurring_transactions"] += int(split_recurring or 0)

    rules = await get_rules_assigning_category(
        session, workspace_id, category_id, include_inactive=True
    )
    return CategoryUsage(**counts, rules=rules)


async def _used_in_category_lines(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> bool:
    """A part of a split transaction or of a split recurring bill uses it."""
    for model in (TransactionCategoryLine, RecurringTransactionCategoryLine):
        found = await session.scalar(
            select(model.id)
            .where(model.workspace_id == workspace_id, model.category_id == category_id)
            .limit(1)
        )
        if found is not None:
            return True
    return False


_CARD_CREDIT_TRANSFER = "A credit on a credit card cannot have a line in a transfer category"


async def _used_in_card_credit_lines(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> bool:
    """A part of a credit on a credit card (or of a template for one) uses it.

    A card statement drops a credit in a transfer-like category as a whole and
    cannot drop only part of one, so such a part may never become transfer-like.
    """
    for line_model, owner_model, owner_fk in (
        (TransactionCategoryLine, Transaction, TransactionCategoryLine.transaction_id),
        (
            RecurringTransactionCategoryLine,
            RecurringTransaction,
            RecurringTransactionCategoryLine.recurring_transaction_id,
        ),
    ):
        found = await session.scalar(
            select(line_model.id)
            .join(owner_model, owner_model.id == owner_fk)
            .join(Account, Account.id == owner_model.account_id)
            .where(
                line_model.workspace_id == workspace_id,
                line_model.category_id == category_id,
                owner_model.type == "credit",
                Account.type == "credit_card",
            )
            .limit(1)
        )
        if found is not None:
            return True
    return False


_Limit = tuple[Decimal, Decimal | None]


async def _merge_budgets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Combine the workspace's effective monthly limits without losing either side.

    Recurring rows change a limit from their month onward; a one-off row only
    overrides its own category for that month. Merge those schedules before
    moving the rows, or a later recurring row would hide the earlier limit.
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
    # Readers take every member's row in a category's latest slot, so the rows
    # of each (month, kind) slot must add up to both categories' limits. Each
    # member keeps one row carrying their own limits, as the unique constraint
    # requires; a limit whose author has no row in the slot (the other
    # category's carried-over default) lands on a kept row, destination first.
    ordered = sorted([*moving, *existing], key=lambda b: (b.month, not b.is_recurring))
    recurring: dict[uuid.UUID, dict[uuid.UUID, _Limit]] = {}
    for (_, is_recurring), slot in groupby(ordered, key=lambda b: (b.month, b.is_recurring)):
        rows = list(slot)
        limits: dict[uuid.UUID, dict[uuid.UUID, _Limit]] = {}
        for budget in rows:
            limits.setdefault(budget.category_id, {})[budget.user_id] = (
                budget.amount, budget.amount_primary,
            )
        if is_recurring:
            # Store immutable amounts before changing any ORM row. An
            # override below must still see each original category's limit.
            recurring.update(limits)
        effective = recurring if is_recurring else recurring | limits

        # Keeping the destination row means no source row is moved onto a
        # slot the same member still holds, which would fail at flush.
        kept: dict[uuid.UUID, Budget] = {}
        for budget in sorted(rows, key=lambda b: b.category_id != destination_id):
            kept.setdefault(budget.user_id, budget)
        fallback = next(iter(kept))
        shares: dict[uuid.UUID, list[_Limit]] = {user_id: [] for user_id in kept}
        for by_user in effective.values():
            for user_id, limit in by_user.items():
                shares[user_id if user_id in kept else fallback].append(limit)

        for user_id, target in kept.items():
            target.category_id = destination_id
            target.amount = sum((amount for amount, _ in shares[user_id]), Decimal("0"))
            primary = [value for _, value in shares[user_id] if value is not None]
            target.amount_primary = (
                sum(primary, Decimal("0")) if len(primary) == len(shares[user_id]) else None
            )
        for budget in rows:
            if kept[budget.user_id] is not budget:
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


def _join_notes(*notes: Optional[str], limit: int) -> Optional[str]:
    """Notes from parts that became one, each kept once, cut to the column."""
    kept = [note.strip() for note in notes if note and note.strip()]
    joined = "; ".join(dict.fromkeys(kept))
    return joined[:limit] or None


async def _move_category_lines(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> None:
    """Move the parts in one category to another, merging where they meet.

    A split that already has a part in the destination gets the moved amount
    (and its note) added to that part rather than a second part in the same
    category, and a split left with a single part becomes a plain row in that
    category. A transaction keeps that last part's note in its own notes; a
    recurring bill has no notes of its own to keep it in.
    """
    for line_model, owner_model, owner_fk in (
        (TransactionCategoryLine, Transaction, "transaction_id"),
        (RecurringTransactionCategoryLine, RecurringTransaction, "recurring_transaction_id"),
    ):
        owner_col = getattr(line_model, owner_fk)
        affected = select(owner_col).where(
            line_model.workspace_id == workspace_id,
            line_model.category_id == category_id,
        )
        lines = (
            await session.scalars(
                select(line_model)
                .where(owner_col.in_(affected))
                .order_by(owner_col, line_model.position)
            )
        ).all()
        collapsed: dict[uuid.UUID, TransactionCategoryLine | RecurringTransactionCategoryLine] = {}
        for owner_id, group in groupby(lines, key=lambda line: getattr(line, owner_fk)):
            parts = list(group)
            kept = next((line for line in parts if line.category_id == destination_id), None)
            remaining = []
            for line in parts:
                if line.category_id not in (category_id, destination_id) or line is kept:
                    remaining.append(line)
                elif kept is None:
                    line.category_id = destination_id
                    kept = line
                    remaining.append(line)
                else:
                    kept.amount += line.amount
                    if kept.share_pct is not None and line.share_pct is not None:
                        kept.share_pct += line.share_pct
                    kept.notes = _join_notes(kept.notes, line.notes, limit=500)
                    await session.delete(line)
            if len(remaining) == 1:
                collapsed[owner_id] = remaining[0]
        if not collapsed:
            continue
        owners = (
            await session.scalars(select(owner_model).where(owner_model.id.in_(collapsed)))
        ).all()
        for owner in owners:
            last = collapsed[owner.id]
            owner.category_id = last.category_id
            owner.has_category_lines = False
            if isinstance(owner, Transaction) and last.notes:
                owner.notes = _join_notes(owner.notes, last.notes, limit=1000)
            await session.delete(last)
    await session.flush()


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
    await _move_category_lines(session, workspace_id, category_id, destination_id)
    await _merge_budgets(session, workspace_id, category_id, destination_id)
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
        if (destination.is_ignored or destination.is_hidden) and await _used_in_category_lines(
            session, workspace_id, category_id
        ):
            raise CategoryTransferError(
                "Transactions split across categories cannot move into an ignored "
                "or hidden category."
            )
        if (
            destination.treat_as_transfer
            and not category.treat_as_transfer
            and await _used_in_card_credit_lines(session, workspace_id, category_id)
        ):
            raise CategoryTransferError(_CARD_CREDIT_TRANSFER)
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

