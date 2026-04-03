import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.budget import Budget
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.budget import BudgetCreate, BudgetUpdate, BudgetVsActual
from app.services.dashboard_service import _get_recurring_projections
from app.services.fx_rate_service import convert


def _primary_amount_expr():
    """Amount in primary currency: uses amount_primary when available, falls back to amount."""
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


async def _get_category(session: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID) -> Optional[Category]:
    result = await session.execute(
        select(Category).where(Category.id == category_id, Category.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _get_category_budgets(
    session: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID
) -> list[Budget]:
    result = await session.execute(
        select(Budget)
        .where(Budget.user_id == user_id, Budget.category_id == category_id)
        .order_by(Budget.month.desc(), Budget.created_at.desc(), Budget.id.desc())
    )
    return list(result.scalars().all())


async def _collapse_budget_rows(
    session: AsyncSession,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    primary_budget: Optional[Budget] = None,
) -> Optional[Budget]:
    budgets = await _get_category_budgets(session, user_id, category_id)
    if not budgets:
        return None

    keeper = primary_budget or budgets[0]
    duplicates = [b for b in budgets if b.id != keeper.id]
    for duplicate in duplicates:
        await session.delete(duplicate)
    return keeper


async def get_budgets(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None
) -> list[Budget]:
    """Return one budget row per category.

    `month` is kept for route compatibility but no longer affects resolution.
    """
    result = await session.execute(
        select(Budget)
        .join(Category, Budget.category_id == Category.id)
        .where(Budget.user_id == user_id, Category.has_budget.is_(True))
        .order_by(Budget.month.desc(), Budget.created_at.desc(), Budget.id.desc())
    )

    seen_categories: set[uuid.UUID] = set()
    budgets: list[Budget] = []
    for budget in result.scalars().all():
        # Keep the newest surviving row for each category.
        if budget.category_id in seen_categories:
            continue
        seen_categories.add(budget.category_id)
        budgets.append(budget)
    return budgets


async def get_budget(
    session: AsyncSession, budget_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Budget]:
    result = await session.execute(
        select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def create_budget(
    session: AsyncSession, user_id: uuid.UUID, data: BudgetCreate
) -> Budget:
    category = await _get_category(session, user_id, data.category_id)
    if not category:
        raise ValueError("Category not found")

    normalized_month = data.month.replace(day=1)
    existing = await _collapse_budget_rows(session, user_id, data.category_id)
    if existing:
        existing.amount = data.amount
        existing.month = normalized_month
        existing.is_recurring = False
        budget = existing
    else:
        budget = Budget(
            user_id=user_id,
            category_id=data.category_id,
            amount=data.amount,
            month=normalized_month,
            is_recurring=False,
        )
        session.add(budget)

    category.has_budget = True
    category.budget_amount = data.amount

    await session.commit()
    await session.refresh(budget)
    return budget


async def update_budget(
    session: AsyncSession, budget_id: uuid.UUID, user_id: uuid.UUID, data: BudgetUpdate
) -> Optional[Budget]:
    budget = await get_budget(session, budget_id, user_id)
    if not budget:
        return None

    category = await _get_category(session, user_id, budget.category_id)
    if not category:
        return None

    if data.amount is not None:
        budget.amount = data.amount
        category.has_budget = True
        category.budget_amount = data.amount

    if data.effective_month is not None:
        budget.month = data.effective_month.replace(day=1)

    budget.is_recurring = False
    await _collapse_budget_rows(session, user_id, budget.category_id, primary_budget=budget)

    await session.commit()
    await session.refresh(budget)
    return budget


async def delete_budget(
    session: AsyncSession, budget_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    budget = await get_budget(session, budget_id, user_id)
    if not budget:
        return False

    category = await _get_category(session, user_id, budget.category_id)
    if category:
        category.has_budget = False
        category.budget_amount = None

    await session.execute(
        delete(Budget).where(Budget.user_id == user_id, Budget.category_id == budget.category_id)
    )
    await session.commit()
    return True


async def get_budget_vs_actual(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None
) -> list[BudgetVsActual]:
    if not month:
        month = date.today().replace(day=1)

    month_start = month.replace(day=1)
    if month.month == 12:
        month_end = month.replace(year=month.year + 1, month=1, day=1)
    else:
        month_end = month.replace(month=month.month + 1, day=1)

    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)
    prev_month_end = month_start

    cats_result = await session.execute(select(Category).where(Category.user_id == user_id))
    all_categories = list(cats_result.scalars().all())
    if not all_categories:
        return []

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    spending_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(_primary_amount_expr()),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "debit",
            Transaction.date >= month_start,
            Transaction.date < month_end,
            Transaction.category_id.isnot(None),
            Transaction.transfer_pair_id.is_(None),
        )
        .group_by(Transaction.category_id)
    )
    spending_map: dict[str, Decimal] = {}
    for row in spending_result.all():
        spending_map[str(row[0])] = abs(row[1] or Decimal("0"))

    projections = await _get_recurring_projections(session, user_id, month_start, month_end)
    for proj in projections:
        if proj["type"] != "debit" or not proj["category_id"]:
            continue
        cat_id = str(proj["category_id"])
        converted, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        spending_map[cat_id] = spending_map.get(cat_id, Decimal("0")) + converted

    prev_spending_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(_primary_amount_expr()),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "debit",
            Transaction.date >= prev_month_start,
            Transaction.date < prev_month_end,
            Transaction.category_id.isnot(None),
            Transaction.transfer_pair_id.is_(None),
        )
        .group_by(Transaction.category_id)
    )
    prev_spending_map: dict[str, Decimal] = {}
    for row in prev_spending_result.all():
        prev_spending_map[str(row[0])] = abs(row[1] or Decimal("0"))

    prev_projections = await _get_recurring_projections(session, user_id, prev_month_start, prev_month_end)
    for proj in prev_projections:
        if proj["type"] != "debit" or not proj["category_id"]:
            continue
        cat_id = str(proj["category_id"])
        converted, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        prev_spending_map[cat_id] = prev_spending_map.get(cat_id, Decimal("0")) + converted

    comparisons = []
    for category in all_categories:
        cat_id = str(category.id)
        actual = spending_map.get(cat_id, Decimal("0"))
        prev_actual = prev_spending_map.get(cat_id, Decimal("0"))
        budget_amount = category.budget_amount if category.has_budget else None

        if actual == 0 and prev_actual == 0 and budget_amount is None:
            continue

        percentage = None
        if budget_amount is not None and budget_amount > 0:
            percentage = round(float(actual / budget_amount * 100), 1)

        comparisons.append(BudgetVsActual(
            category_id=category.id,
            category_name=category.name,
            category_icon=category.icon,
            category_color=category.color,
            budget_amount=budget_amount,
            actual_amount=actual,
            prev_month_amount=prev_actual,
            percentage_used=percentage,
            is_recurring=False,
        ))

    return sorted(comparisons, key=lambda x: float(x.actual_amount), reverse=True)
