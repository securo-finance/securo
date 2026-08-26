import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.budget import BudgetCreate, BudgetUpdate, BudgetVsActual
from app.services._query_filters import (
    counts_as_user_pnl,
    owner_split_offset_by_category,
    reporting_date_col,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.dashboard_service import (
    _counts_as_user_pnl_row,
    _get_forecast_transactions,
    _get_recurring_projections,
)
from app.services.fx_rate_service import convert
from app.core.config import get_settings


def _primary_amount_expr():
    """Amount in primary currency: uses amount_primary when available, falls back to amount."""
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


async def _build_budget_map(
    session: AsyncSession, workspace_id: uuid.UUID, month_start: date
) -> dict[str, tuple[Decimal, bool]]:
    """Build a map of category_id -> (amount, is_recurring) for the given month.

    Resolution order:
    1. Month-specific override (is_recurring=false, month=M) takes priority
    2. Most recent recurring default (is_recurring=true, month<=M) as fallback
    """
    budget_map: dict[str, tuple[Decimal, bool]] = {}

    # Query 1: Get effective recurring defaults (most recent per category where month <= M)
    # Use a subquery to get the max month per category for recurring budgets
    max_month_subq = (
        select(
            Budget.category_id,
            func.max(Budget.month).label("max_month"),
        )
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == True,  # noqa: E712
            Budget.month <= month_start,
        )
        .group_by(Budget.category_id)
        .subquery()
    )

    recurring_result = await session.execute(
        select(Budget)
        .join(
            max_month_subq,
            and_(
                Budget.category_id == max_month_subq.c.category_id,
                Budget.month == max_month_subq.c.max_month,
            ),
        )
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == True,  # noqa: E712
        )
    )
    for b in recurring_result.scalars().all():
        budget_map[str(b.category_id)] = (b.amount, True)

    # Query 2: Month-specific overrides (take priority over recurring)
    overrides_result = await session.execute(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.month == month_start,
        )
    )
    for b in overrides_result.scalars().all():
        budget_map[str(b.category_id)] = (b.amount, False)

    return budget_map


async def get_budgets(
    session: AsyncSession, workspace_id: uuid.UUID, month: Optional[date] = None
) -> list[Budget]:
    if not month:
        query = select(Budget).where(Budget.workspace_id == workspace_id)
        result = await session.execute(query.order_by(Budget.month.desc()))
        return list(result.scalars().all())

    month_start = month.replace(day=1)

    # Get month-specific overrides
    overrides_result = await session.execute(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.month == month_start,
        )
    )
    overrides = list(overrides_result.scalars().all())
    override_category_ids = {str(b.category_id) for b in overrides}

    # Get effective recurring defaults for this month
    max_month_subq = (
        select(
            Budget.category_id,
            func.max(Budget.month).label("max_month"),
        )
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == True,  # noqa: E712
            Budget.month <= month_start,
        )
        .group_by(Budget.category_id)
        .subquery()
    )

    recurring_result = await session.execute(
        select(Budget)
        .join(
            max_month_subq,
            and_(
                Budget.category_id == max_month_subq.c.category_id,
                Budget.month == max_month_subq.c.max_month,
            ),
        )
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == True,  # noqa: E712
        )
    )
    recurring = [
        b for b in recurring_result.scalars().all()
        if str(b.category_id) not in override_category_ids
    ]

    return sorted(overrides + recurring, key=lambda b: b.month, reverse=True)


async def get_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Budget]:
    result = await session.execute(
        select(Budget).where(Budget.id == budget_id, Budget.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def create_budget(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BudgetCreate,
) -> Budget:
    budget = Budget(
        user_id=user_id,
        workspace_id=workspace_id,
        category_id=data.category_id,
        amount=data.amount,
        month=data.month.replace(day=1),
        is_recurring=data.is_recurring,
    )
    session.add(budget)
    await session.commit()
    await session.refresh(budget)
    return budget


async def update_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID, data: BudgetUpdate
) -> Optional[Budget]:
    budget = await get_budget(session, budget_id, workspace_id)
    if not budget:
        return None

    if budget.is_recurring and data.effective_month:
        effective = data.effective_month.replace(day=1)
        if effective != budget.month:
            # Create a new recurring record with new effective-from month
            new_budget = Budget(
                user_id=budget.user_id,
                workspace_id=budget.workspace_id,
                category_id=budget.category_id,
                amount=data.amount if data.amount is not None else budget.amount,
                month=effective,
                is_recurring=True,
            )
            session.add(new_budget)
            await session.commit()
            await session.refresh(new_budget)
            return new_budget

    # Update in place (non-recurring, or same effective-from month)
    for key, value in data.model_dump(exclude_unset=True, exclude={"effective_month"}).items():
        setattr(budget, key, value)

    await session.commit()
    await session.refresh(budget)
    return budget


async def delete_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    budget = await get_budget(session, budget_id, workspace_id)
    if not budget:
        return False

    await session.delete(budget)
    await session.commit()
    return True


async def get_budget_vs_actual(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
) -> list[BudgetVsActual]:
    if not month:
        month = date.today().replace(day=1)

    month_start = month.replace(day=1)
    if month.month == 12:
        month_end = month.replace(year=month.year + 1, month=1, day=1)
    else:
        month_end = month.replace(month=month.month + 1, day=1)

    # Previous month range
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)
    prev_month_end = month_start

    # Get all categories for this workspace with their groups
    cats_result = await session.execute(
        select(Category, CategoryGroup)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(Category.workspace_id == workspace_id)
    )
    all_categories = cats_result.all()

    if not all_categories:
        return []

    # Get budgets for this month (with recurring resolution)
    budget_map = await _build_budget_map(session, workspace_id, month_start)

    # Get user's primary currency for FX conversion + reporting mode
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)

    # Get actual spending by category for this month (exclude transfer pairs)
    # Use amount_primary for multi-currency support
    spending_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(_primary_amount_expr()),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            report_date >= month_start,
            report_date < month_end,
            Transaction.category_id.isnot(None),
            report_date <= date.today(),
            Transaction.status == "posted",
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id)
    )
    spending_map: dict[str, Decimal] = {}
    for row in spending_result.all():
        spending_map[str(row[0])] = abs(row[1] or Decimal("0"))

    # Subtract non-owner shares of own splits — only the user's share counts.
    own_offset = await owner_split_offset_by_category(
        session, user_id, month_start, month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        workspace_id=workspace_id,
    )
    for cat_uuid, total in own_offset.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        if cat_id in spending_map:
            spending_map[cat_id] -= Decimal(str(total))
            if spending_map[cat_id] <= 0:
                spending_map.pop(cat_id)

    # Layer in the user's share from group splits — concert tickets
    # paid by a friend are still the user's expense in the budget
    # picture for the matching category. FX-convert per currency to
    # match the rest of the budget (everything else is in primary).
    from app.services._query_filters import viewer_shared_spending_by_category

    shared_by_cat = await viewer_shared_spending_by_category(
        session, user_id, month_start, month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    for cat_uuid, total in shared_by_cat.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        spending_map[cat_id] = spending_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    projected_spending_map = dict(spending_map)

    # Add projected recurring transactions for this month (converted to primary currency)
    projections = await _get_recurring_projections(session, workspace_id, month_start, month_end)
    for proj in projections:
        if proj["type"] != "debit" or not proj["category_id"]:
            continue
        cat_id = str(proj["category_id"])
        converted, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        projected_spending_map[cat_id] = projected_spending_map.get(cat_id, Decimal("0")) + converted

    forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, month_start, month_end,
        range_date_col=report_date,
    )
    for tx in forecast_transactions:
        if tx.type != "debit" or not tx.category_id or not _counts_as_user_pnl_row(tx):
            continue
        cat_id = str(tx.category_id)
        amount = tx.amount_primary
        if amount is not None:
            converted = abs(Decimal(str(amount)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
        projected_spending_map[cat_id] = projected_spending_map.get(cat_id, Decimal("0")) + converted

    # Get previous month spending by category (exclude transfer pairs)
    # Use amount_primary for multi-currency support
    prev_spending_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(_primary_amount_expr()),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            report_date >= prev_month_start,
            report_date < prev_month_end,
            Transaction.category_id.isnot(None),
            report_date <= date.today(),
            Transaction.status == "posted",
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id)
    )
    prev_spending_map: dict[str, Decimal] = {}
    for row in prev_spending_result.all():
        prev_spending_map[str(row[0])] = abs(row[1] or Decimal("0"))

    prev_own_offset = await owner_split_offset_by_category(
        session, user_id, prev_month_start, prev_month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        workspace_id=workspace_id,
    )
    for cat_uuid, total in prev_own_offset.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        if cat_id in prev_spending_map:
            prev_spending_map[cat_id] -= Decimal(str(total))
            if prev_spending_map[cat_id] <= 0:
                prev_spending_map.pop(cat_id)

    # Same shared-share layer for the previous month so the trend
    # comparison is apples-to-apples.
    prev_shared_by_cat = await viewer_shared_spending_by_category(
        session, user_id, prev_month_start, prev_month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    for cat_uuid, total in prev_shared_by_cat.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        prev_spending_map[cat_id] = prev_spending_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    projected_prev_spending_map = dict(prev_spending_map)

    # Add projected recurring transactions for previous month (converted to primary currency)
    prev_projections = await _get_recurring_projections(session, workspace_id, prev_month_start, prev_month_end)
    for proj in prev_projections:
        if proj["type"] != "debit" or not proj["category_id"]:
            continue
        cat_id = str(proj["category_id"])
        converted, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        projected_prev_spending_map[cat_id] = projected_prev_spending_map.get(cat_id, Decimal("0")) + converted

    prev_forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, prev_month_start, prev_month_end,
        range_date_col=report_date,
    )
    for tx in prev_forecast_transactions:
        if tx.type != "debit" or not tx.category_id or not _counts_as_user_pnl_row(tx):
            continue
        cat_id = str(tx.category_id)
        amount = tx.amount_primary
        if amount is not None:
            converted = abs(Decimal(str(amount)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
        projected_prev_spending_map[cat_id] = projected_prev_spending_map.get(cat_id, Decimal("0")) + converted

    comparisons = []
    for category, group in all_categories:
        cat_id = str(category.id)
        actual = spending_map.get(cat_id, Decimal("0"))
        projected = projected_spending_map.get(cat_id, Decimal("0"))
        prev_actual = prev_spending_map.get(cat_id, Decimal("0"))
        projected_prev = projected_prev_spending_map.get(cat_id, Decimal("0"))
        budget_entry = budget_map.get(cat_id)
        budget_amount = budget_entry[0] if budget_entry else None
        is_recurring = budget_entry[1] if budget_entry else False

        # Skip categories with no spending in either month and no budget
        if actual == 0 and projected == 0 and prev_actual == 0 and projected_prev == 0 and budget_amount is None:
            continue

        percentage = None
        if budget_amount and budget_amount > 0:
            percentage = round(float(projected / budget_amount * 100), 1)

        comparisons.append(BudgetVsActual(
            category_id=category.id,
            category_name=category.name,
            category_icon=category.icon,
            category_color=category.color,
            group_id=group.id if group else None,
            group_name=group.name if group else None,
            budget_amount=budget_amount,
            actual_amount=actual,
            projected_amount=projected,
            prev_month_amount=prev_actual,
            projected_prev_month_amount=projected_prev,
            percentage_used=percentage,
            is_recurring=is_recurring,
        ))

    return sorted(comparisons, key=lambda x: float(x.actual_amount), reverse=True)
