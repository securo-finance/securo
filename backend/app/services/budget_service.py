import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select, func, and_, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.budget import (
    MAX_BUDGET_AMOUNT,
    BudgetBatchCreate,
    BudgetCreate,
    BudgetRead,
    BudgetSuggestion,
    BudgetSuggestionResponse,
    BudgetUpdate,
    BudgetVsActual,
)
from app.services.viewer_currency import ViewerCurrency, viewer_currency
from app.services._query_filters import (
    counts_as_user_pnl,
    forecast_owner_share_fractions,
    line_category_id,
    line_primary_or_amount,
    owner_split_offset_by_category,
    owner_split_offset_by_category_monthly,
    reporting_date_col,
    reporting_date_value,
    user_pnl_parts,
    viewer_shared_spending_by_category_monthly,
    with_category_lines,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.dashboard_service import (
    _get_forecast_transactions,
    _get_recurring_projections,
)
from app.services.fx_rate_service import convert
from app.core.config import get_settings

MAX_ROLLOVER_LOOKBACK_MONTHS = 24
_CENT = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


async def _primary_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def amount_in_currency(
    session: AsyncSession,
    amount: Decimal,
    currency: Optional[str],
    target_currency: str,
    month: date,
) -> Decimal:
    """A budget limit in `target_currency`, at the rate of its month.

    A limit keeps the number and currency it was typed in, so switching the
    display currency and back gives the same number. A row with no currency
    reads as the viewer's own.
    """
    if not currency or currency == target_currency:
        return amount
    converted, _ = await convert(
        session, amount, currency, target_currency, min(month, app_today())
    )
    return converted


async def budget_reads(
    session: AsyncSession,
    budgets: list[Budget],
    user_id: uuid.UUID,
    month: Optional[date] = None,
) -> list[BudgetRead]:
    """Budgets as the API returns them, with `amount_primary` in the viewer's currency.

    Converted at the rate of `month` when one was asked for, the same rate
    the comparison for that month uses, else at each budget's own month.
    """
    primary = await _primary_currency(session, user_id)
    reads = []
    for budget in budgets:
        read = BudgetRead.model_validate(budget)
        read.amount_primary = await amount_in_currency(
            session,
            budget.amount,
            budget.currency,
            primary,
            month.replace(day=1) if month else budget.month,
        )
        reads.append(read)
    return reads


async def _build_budget_map(
    session: AsyncSession, workspace_id: uuid.UUID, month_start: date
) -> dict[str, tuple[Decimal, bool, Optional[str], bool, bool]]:
    """Build a map of category_id -> (amount, is_recurring, currency, rollover, reset_rollover).

    Resolution order:
    1. Month-specific override (is_recurring=false, month=M) takes priority
    2. Most recent recurring default (is_recurring=true, month<=M) as fallback

    For recurring rows, ``reset_rollover`` applies ONLY in the exact month
    where ``Budget.month == month_start`` so a one-month boundary reset does
    not clear carryover in every subsequent month.
    """
    budget_map: dict[str, tuple[Decimal, bool, Optional[str], bool, bool]] = {}

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
        .order_by(Budget.created_at.asc(), Budget.id.asc())
    )
    for b in recurring_result.scalars().all():
        budget_map[str(b.category_id)] = (
            b.amount,
            True,
            b.currency,
            bool(b.rollover),
            bool(b.reset_rollover and b.month == month_start),
        )

    # Query 2: Month-specific overrides (take priority over recurring)
    overrides_result = await session.execute(
        select(Budget)
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.month == month_start,
        )
        .order_by(Budget.created_at.asc(), Budget.id.asc())
    )
    for b in overrides_result.scalars().all():
        budget_map[str(b.category_id)] = (
            b.amount,
            False,
            b.currency,
            bool(b.rollover),
            bool(b.reset_rollover),
        )

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
    recurring: list[Budget] = []
    for b in recurring_result.scalars().all():
        if str(b.category_id) in override_category_ids:
            continue
        if b.reset_rollover and b.month != month_start:
            # Project reset_rollover=False for inherited recurring rows without
            # mutating the session-tracked ORM instance in the identity map.
            b = Budget(
                id=b.id,
                user_id=b.user_id,
                workspace_id=b.workspace_id,
                category_id=b.category_id,
                amount=b.amount,
                month=b.month,
                is_recurring=b.is_recurring,
                rollover=b.rollover,
                reset_rollover=False,
                currency=b.currency,
                amount_primary=b.amount_primary,
                created_at=b.created_at,
            )
        recurring.append(b)

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
    """Create a budget for a category of the workspace.

    The category is checked here, as the batch does, so an id from another
    workspace or an unknown one fails with a readable error instead of the
    driver's.
    """
    known = (
        await session.execute(
            select(Category.id).where(
                Category.id == data.category_id,
                Category.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if known is None:
        raise ValueError("Category not found")

    budget = Budget(
        user_id=user_id,
        workspace_id=workspace_id,
        category_id=data.category_id,
        amount=data.amount,
        currency=await _primary_currency(session, user_id),
        month=data.month.replace(day=1),
        is_recurring=data.is_recurring,
        rollover=data.rollover,
        reset_rollover=data.reset_rollover,
    )
    session.add(budget)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ValueError("A budget for this category already exists for this month")
    await session.refresh(budget)
    return budget


async def update_budget(
    session: AsyncSession,
    budget_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: BudgetUpdate,
    user_id: Optional[uuid.UUID] = None,
) -> Optional[Budget]:
    """Change a budget; a new amount is in the editor's currency (`user_id`)."""
    budget = await get_budget(session, budget_id, workspace_id)
    if not budget:
        return None
    currency = (
        await _primary_currency(session, user_id or budget.user_id)
        if data.amount is not None
        else budget.currency
    )

    if budget.is_recurring and data.effective_month:
        effective = data.effective_month.replace(day=1)
        if effective != budget.month:
            # Create a new recurring record with new effective-from month
            new_budget = Budget(
                user_id=budget.user_id,
                workspace_id=budget.workspace_id,
                category_id=budget.category_id,
                amount=data.amount if data.amount is not None else budget.amount,
                currency=currency,
                month=effective,
                is_recurring=True,
                rollover=data.rollover if data.rollover is not None else budget.rollover,
                reset_rollover=(
                    data.reset_rollover if data.reset_rollover is not None else False
                ),
            )
            session.add(new_budget)
            await session.commit()
            await session.refresh(new_budget)
            return new_budget

    # Update in place (non-recurring, or same effective-from month)
    for key, value in data.model_dump(exclude_unset=True, exclude={"effective_month"}).items():
        setattr(budget, key, value)
    budget.currency = currency

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
        month = app_today().replace(day=1)

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
    category_by_id = {str(category.id): category for category, _ in all_categories}

    # Get budgets for this month (with recurring resolution)
    budget_map = await _build_budget_map(session, workspace_id, month_start)

    # Viewer's primary currency for FX conversion + reporting mode
    primary_currency = await _primary_currency(session, user_id)
    viewer = await viewer_currency(session, primary_currency)
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)

    # Get actual spending by category for this month (exclude transfer pairs)
    # Net spending = Debits - Credits (refunds offset expenses)
    # Use amount_primary for multi-currency support
    spending_result = await session.execute(
        with_category_lines(
            select(
                line_category_id(),
                func.sum(
                    case(
                        (Transaction.type == "debit", line_primary_or_amount(viewer)),
                        (Transaction.type == "credit", -line_primary_or_amount(viewer)),
                        else_=0,
                    )
                ),
            ).select_from(Transaction)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            report_date >= month_start,
            report_date < month_end,
            line_category_id().isnot(None),
            report_date <= app_today(),
            Transaction.status == "posted",
            counts_as_user_pnl(line_category_id()),
        )
        .group_by(line_category_id())
    )
    spending_map: dict[str, Decimal] = {}
    for row in spending_result.all():
        net = row[1] or Decimal("0")
        if net > 0:
            spending_map[str(row[0])] = net

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
    from app.services._query_filters import (
        resolve_viewer_category_map,
        viewer_shared_spending_by_category,
    )

    shared_by_cat = await viewer_shared_spending_by_category(
        session, user_id, month_start, month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        exclude_workspace_id=workspace_id,
    )
    if shared_by_cat:
        shared_cat_map = await resolve_viewer_category_map(
            session, shared_by_cat.keys(), workspace_id
        )
        for cat_uuid, total in shared_by_cat.items():
            mapped_cat = shared_cat_map.get(cat_uuid) if cat_uuid is not None else None
            if mapped_cat is None:
                continue
            cat_id = str(mapped_cat.id)
            spending_map[cat_id] = spending_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    projected_spending_map = dict(spending_map)

    # Add projected recurring transactions for this month (converted to primary currency)
    projections = await _get_recurring_projections(
        session, workspace_id, month_start, month_end, accounting_mode=accounting_mode,
    )
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
    forecast_fractions = await forecast_owner_share_fractions(
        session, user_id, forecast_transactions
    )
    for tx in forecast_transactions:
        if tx.type != "debit":
            continue
        parts = [part for part in user_pnl_parts(tx) if part[0] is not None]
        if not parts:
            continue
        amount = await viewer.convert(
            session, tx.user_id, tx.amount_primary, tx.date,
            currency=tx.currency, amount=tx.amount,
        )
        if amount is not None:
            converted = abs(Decimal(str(amount)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
        converted *= forecast_fractions.get(tx.id, 1)
        for part_category_id, _, ratio in parts:
            cat_id = str(part_category_id)
            projected_spending_map[cat_id] = projected_spending_map.get(cat_id, Decimal("0")) + converted * ratio
    # A negative owner share can pull a category below zero; like posted
    # spending, a category that ends up nonpositive drops out.
    for cat_id, total in list(projected_spending_map.items()):
        if total <= 0:
            del projected_spending_map[cat_id]

    # Get previous month spending by category (exclude transfer pairs)
    # Net spending = Debits - Credits (refunds offset expenses)
    # Use amount_primary for multi-currency support
    prev_spending_result = await session.execute(
        with_category_lines(
            select(
                line_category_id(),
                func.sum(
                    case(
                        (Transaction.type == "debit", line_primary_or_amount(viewer)),
                        (Transaction.type == "credit", -line_primary_or_amount(viewer)),
                        else_=0,
                    )
                ),
            ).select_from(Transaction)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            report_date >= prev_month_start,
            report_date < prev_month_end,
            line_category_id().isnot(None),
            report_date <= app_today(),
            Transaction.status == "posted",
            counts_as_user_pnl(line_category_id()),
        )
        .group_by(line_category_id())
    )
    prev_spending_map: dict[str, Decimal] = {}
    for row in prev_spending_result.all():
        net = row[1] or Decimal("0")
        if net > 0:
            prev_spending_map[str(row[0])] = net

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
        exclude_workspace_id=workspace_id,
    )
    if prev_shared_by_cat:
        prev_shared_cat_map = await resolve_viewer_category_map(
            session, prev_shared_by_cat.keys(), workspace_id
        )
        for cat_uuid, total in prev_shared_by_cat.items():
            mapped_cat = prev_shared_cat_map.get(cat_uuid) if cat_uuid is not None else None
            if mapped_cat is None:
                continue
            cat_id = str(mapped_cat.id)
            prev_spending_map[cat_id] = prev_spending_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    projected_prev_spending_map = dict(prev_spending_map)

    # Add projected recurring transactions for previous month (converted to primary currency)
    prev_projections = await _get_recurring_projections(
        session, workspace_id, prev_month_start, prev_month_end, accounting_mode=accounting_mode,
    )
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
    prev_forecast_fractions = await forecast_owner_share_fractions(
        session, user_id, prev_forecast_transactions
    )
    for tx in prev_forecast_transactions:
        if tx.type != "debit":
            continue
        parts = [part for part in user_pnl_parts(tx) if part[0] is not None]
        if not parts:
            continue
        amount = await viewer.convert(
            session, tx.user_id, tx.amount_primary, tx.date,
            currency=tx.currency, amount=tx.amount,
        )
        if amount is not None:
            converted = abs(Decimal(str(amount)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
        converted *= prev_forecast_fractions.get(tx.id, 1)
        for part_category_id, _, ratio in parts:
            cat_id = str(part_category_id)
            projected_prev_spending_map[cat_id] = projected_prev_spending_map.get(cat_id, Decimal("0")) + converted * ratio
    # A negative owner share can pull a category below zero; like posted
    # spending, a category that ends up nonpositive drops out.
    for cat_id, total in list(projected_prev_spending_map.items()):
        if total <= 0:
            del projected_prev_spending_map[cat_id]

    carryover_map = await _compute_carryover_map(
        session,
        workspace_id,
        user_id,
        month_start,
        budget_map,
        category_by_id,
        primary_currency,
        accounting_mode,
        viewer=viewer,
    )

    comparisons = []
    for category, group in all_categories:
        cat_id = str(category.id)
        actual = _q(Decimal(str(spending_map.get(cat_id, Decimal("0.00")))))
        projected = _q(Decimal(str(projected_spending_map.get(cat_id, Decimal("0.00")))))
        prev_actual = _q(Decimal(str(prev_spending_map.get(cat_id, Decimal("0.00")))))
        projected_prev = _q(Decimal(str(projected_prev_spending_map.get(cat_id, Decimal("0.00")))))
        budget_entry = budget_map.get(cat_id)
        budget_amount = None
        is_recurring = False
        rollover = False
        reset_rollover = False
        if budget_entry:
            amount, is_recurring, currency, rollover, reset_rollover = budget_entry
            # Spending is in the viewer's currency, so the limit is too.
            budget_amount = _q(
                await amount_in_currency(
                    session, amount, currency, primary_currency, month_start
                )
            )
        carryover_amount = _q(carryover_map.get(cat_id, Decimal("0.00")))
        effective_budget_amount = (
            _q(budget_amount + carryover_amount) if budget_amount is not None else None
        )

        # Skip categories with no spending in either month and no budget
        if actual == 0 and projected == 0 and prev_actual == 0 and projected_prev == 0 and budget_amount is None:
            continue

        percentage = None
        if effective_budget_amount is not None and effective_budget_amount > 0:
            percentage = round(float(projected / effective_budget_amount * 100), 1)

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
            rollover=rollover,
            reset_rollover=reset_rollover,
            carryover_amount=carryover_amount,
            effective_budget_amount=effective_budget_amount,
        ))

    return sorted(comparisons, key=lambda x: float(x.actual_amount), reverse=True)


async def _resolve_budget_maps_in_range(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    range_start: date,
    range_end: date,
) -> dict[date, dict[str, tuple[Decimal, bool, Optional[str], bool, bool]]]:
    """Resolve effective budgets per category for every month in ``[range_start, range_end)`` in 2 queries."""
    start = range_start.replace(day=1)
    end = range_end.replace(day=1)
    months: list[date] = []
    cur = start
    while cur < end:
        months.append(cur)
        cur = _add_months(cur, 1)
    if not months:
        return {}

    recurring_result = await session.execute(
        select(Budget)
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == True,  # noqa: E712
            Budget.month < end,
        )
        .order_by(Budget.month.asc(), Budget.created_at.asc(), Budget.id.asc())
    )
    recurring_by_cat: dict[str, list[Budget]] = {}
    for b in recurring_result.scalars().all():
        recurring_by_cat.setdefault(str(b.category_id), []).append(b)

    overrides_result = await session.execute(
        select(Budget)
        .where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.month >= start,
            Budget.month < end,
        )
        .order_by(Budget.created_at.asc(), Budget.id.asc())
    )
    overrides_by_month: dict[date, list[Budget]] = {}
    for b in overrides_result.scalars().all():
        overrides_by_month.setdefault(b.month, []).append(b)

    maps: dict[date, dict[str, tuple[Decimal, bool, Optional[str], bool, bool]]] = {
        m: {} for m in months
    }
    for cat_id, rec_rows in recurring_by_cat.items():
        idx = -1
        for m in months:
            while idx + 1 < len(rec_rows) and rec_rows[idx + 1].month <= m:
                idx += 1
            if idx >= 0:
                b = rec_rows[idx]
                maps[m][cat_id] = (
                    b.amount,
                    True,
                    b.currency,
                    bool(b.rollover),
                    bool(b.reset_rollover and b.month == m),
                )

    for m, ov_rows in overrides_by_month.items():
        if m not in maps:
            continue
        for b in ov_rows:
            maps[m][str(b.category_id)] = (
                b.amount,
                False,
                b.currency,
                bool(b.rollover),
                bool(b.reset_rollover),
            )

    return maps


async def _compute_monthly_spending_map(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    needed_months: set[date],
    category_by_id: dict[str, Category],
    primary_currency: str,
    accounting_mode: str,
    viewer: Optional[ViewerCurrency] = None,
) -> dict[date, dict[str, Decimal]]:
    """Compute batched 5-layer monthly net spending per category for ``needed_months``.

    Applies the same base as ``get_budget_vs_actual``: accrual/cash reporting
    dates, viewer currency conversion, owner split offsets, shared workspace
    shares, and ``app_today()`` cutoff so recurring/forecast projections only
    apply to months with ``month_end > app_today()``.
    """
    if not needed_months:
        return {}

    if viewer is None:
        viewer = await viewer_currency(session, primary_currency)

    min_start = min(needed_months)
    max_end = _add_months(max(needed_months), 1)
    today = app_today()
    report_date = reporting_date_col(accounting_mode)

    # Layer 1: Local posted PnL rolled up by month in Python before net > 0 clamp
    daily_result = await session.execute(
        with_category_lines(
            select(
                line_category_id(),
                report_date,
                func.sum(
                    case(
                        (Transaction.type == "debit", line_primary_or_amount(viewer)),
                        (Transaction.type == "credit", -line_primary_or_amount(viewer)),
                        else_=0,
                    )
                ),
            ).select_from(Transaction)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            report_date >= min_start,
            report_date < max_end,
            line_category_id().isnot(None),
            report_date <= today,
            Transaction.status == "posted",
            counts_as_user_pnl(line_category_id()),
        )
        .group_by(line_category_id(), report_date)
    )
    raw_posted: dict[date, dict[str, Decimal]] = {}
    for cat_uuid, d, net in daily_result.all():
        if isinstance(d, str):
            d = date.fromisoformat(d)
        m_start = d.replace(day=1)
        if m_start not in needed_months:
            continue
        bucket = raw_posted.setdefault(m_start, {})
        cat_id = str(cat_uuid)
        bucket[cat_id] = bucket.get(cat_id, Decimal("0")) + Decimal(str(net or 0))

    spending_by_month: dict[date, dict[str, Decimal]] = {}
    for m_start, cat_totals in raw_posted.items():
        for cat_id, net in cat_totals.items():
            if net > 0:
                spending_by_month.setdefault(m_start, {})[cat_id] = net

    # Layer 2: Subtract non-owner shares on owner-side split transactions
    own_offsets = await owner_split_offset_by_category_monthly(
        session,
        user_id,
        min_start,
        max_end,
        use_effective_date=(accounting_mode == "accrual"),
        primary_currency=primary_currency,
        workspace_id=workspace_id,
    )
    for m_start, cat_offsets in own_offsets.items():
        if m_start not in needed_months:
            continue
        m_map = spending_by_month.get(m_start)
        if not m_map:
            continue
        for cat_uuid, total in cat_offsets.items():
            if cat_uuid is None:
                continue
            cat_id = str(cat_uuid)
            if cat_id in m_map:
                m_map[cat_id] -= Decimal(str(total))
                if m_map[cat_id] <= 0:
                    m_map.pop(cat_id)

    # Layer 3: Cross-workspace shared spending in primary currency
    shared_monthly = await viewer_shared_spending_by_category_monthly(
        session,
        user_id,
        min_start,
        max_end,
        use_effective_date=(accounting_mode == "accrual"),
        primary_currency=primary_currency,
        exclude_workspace_id=workspace_id,
    )
    for m_start, cat_totals in shared_monthly.items():
        if m_start not in needed_months:
            continue
        for cat_uuid, total in cat_totals.items():
            if cat_uuid is None:
                continue
            cat_id = str(cat_uuid)
            cat_obj = category_by_id.get(cat_id)
            if cat_obj is None or cat_obj.treat_as_transfer or cat_obj.is_ignored:
                continue
            m_map = spending_by_month.setdefault(m_start, {})
            m_map[cat_id] = m_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    # Layers 4 & 5: Recurring & forecast projections only when month_end > app_today()
    proj_months = {m for m in needed_months if _add_months(m, 1) > today}
    if proj_months:
        p_start = min(proj_months)
        p_end = _add_months(max(proj_months), 1)
        projections = await _get_recurring_projections(
            session,
            workspace_id,
            p_start,
            p_end,
            accounting_mode=accounting_mode,
        )
        for proj in projections:
            if proj["type"] != "debit" or not proj["category_id"]:
                continue
            m_start = proj["date"].replace(day=1)
            if m_start not in proj_months:
                continue
            cat_id = str(proj["category_id"])
            converted, _ = await convert(
                session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
            )
            m_map = spending_by_month.setdefault(m_start, {})
            m_map[cat_id] = m_map.get(cat_id, Decimal("0")) + converted

        forecast_transactions = await _get_forecast_transactions(
            session,
            workspace_id,
            p_start,
            p_end,
            range_date_col=report_date,
        )
        forecast_fractions = await forecast_owner_share_fractions(
            session, user_id, forecast_transactions
        )
        for tx in forecast_transactions:
            if tx.type != "debit":
                continue
            m_start = reporting_date_value(tx, accounting_mode).replace(day=1)
            if m_start not in proj_months:
                continue
            parts = [part for part in user_pnl_parts(tx) if part[0] is not None]
            if not parts:
                continue
            amount = await viewer.convert(
                session, tx.user_id, tx.amount_primary, tx.date,
                currency=tx.currency, amount=tx.amount,
            )
            if amount is not None:
                converted = abs(Decimal(str(amount)))
            else:
                converted, _ = await convert(
                    session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
                )
            converted *= forecast_fractions.get(tx.id, 1)
            m_map = spending_by_month.setdefault(m_start, {})
            for part_category_id, _, ratio in parts:
                cat_id = str(part_category_id)
                m_map[cat_id] = m_map.get(cat_id, Decimal("0")) + converted * ratio

        for m_start in proj_months:
            m_map = spending_by_month.get(m_start)
            if not m_map:
                continue
            for cat_id, total in list(m_map.items()):
                if total <= 0:
                    del m_map[cat_id]

    for m_start in list(spending_by_month.keys()):
        m_map = spending_by_month[m_start]
        for cat_id in list(m_map.keys()):
            quantized = _q(m_map[cat_id])
            if quantized <= 0:
                del m_map[cat_id]
            else:
                m_map[cat_id] = quantized

    return spending_by_month


async def _compute_carryover_map(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    target_month: date,
    target_budget_map: dict[str, tuple[Decimal, bool, Optional[str], bool, bool]],
    category_by_id: dict[str, Category],
    primary_currency: str,
    accounting_mode: str,
    viewer: Optional[ViewerCurrency] = None,
) -> dict[str, Decimal]:
    """Compute carryover entering ``target_month`` for opt-in rollover categories.

    Returns ``{}`` with zero extra queries when no category in ``target_budget_map``
    has ``amount > 0``, ``rollover=True``, and ``reset_rollover=False``.
    """
    candidates = [
        cat_id
        for cat_id, (amt, _rec, _cur, rollover, reset) in target_budget_map.items()
        if amt > 0 and rollover and not reset
    ]
    if not candidates:
        return {}

    lookback_start = _add_months(target_month, -MAX_ROLLOVER_LOOKBACK_MONTHS)
    budget_maps = await _resolve_budget_maps_in_range(
        session, workspace_id, lookback_start, target_month
    )

    chain_by_cat: dict[str, list[date]] = {}
    for cat_id in candidates:
        months_list: list[date] = []
        for offset in range(1, MAX_ROLLOVER_LOOKBACK_MONTHS + 1):
            m = _add_months(target_month, -offset)
            entry = budget_maps.get(m, {}).get(cat_id)
            if entry is None:
                break
            b_amt, _b_rec, _b_cur, b_rollover, b_reset = entry
            if b_amt <= 0 or not b_rollover:
                break
            months_list.append(m)
            if b_reset:
                break
        if months_list:
            chain_by_cat[cat_id] = months_list

    all_needed_months = {m for months_list in chain_by_cat.values() for m in months_list}
    if not all_needed_months:
        return {}

    spending_by_month = await _compute_monthly_spending_map(
        session,
        workspace_id,
        user_id,
        all_needed_months,
        category_by_id,
        primary_currency,
        accounting_mode,
        viewer=viewer,
    )

    carryover_map: dict[str, Decimal] = {}
    for cat_id, months_list in chain_by_cat.items():
        carry = Decimal("0.00")
        for m in reversed(months_list):
            b_amt, _b_rec, b_cur, _b_rollover, _b_reset = budget_maps[m][cat_id]
            base = _q(
                await amount_in_currency(session, b_amt, b_cur, primary_currency, m)
            )
            spend = _q(spending_by_month.get(m, {}).get(cat_id, Decimal("0.00")))
            carry = _q(carry + base - spend)
        carryover_map[cat_id] = carry
    return carryover_map


def _add_months(d: date, n: int) -> date:
    """First day of the month n months after d (n may be negative)."""
    index = d.year * 12 + d.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


async def get_budget_suggestions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    months: int = 3,
) -> BudgetSuggestionResponse:
    """Suggest budget amounts from average spending over recent months.

    The window covers the ``months`` calendar months immediately before
    ``month`` (the target month, default: current). Any month that is not
    fully in the past yet is excluded so a partial month never skews the
    average. The suggested amount is the net spending of the window
    divided by the number of analyzed months, using the same base as the
    budget vs. actual comparison (posted PnL transactions, credit card
    accounting mode, split offsets, shared workspace shares, primary
    currency).
    """
    if not month:
        month = app_today().replace(day=1)
    target = month.replace(day=1)
    months = max(1, min(int(months), 24))
    today = app_today()

    analyzed = [
        _add_months(target, -offset)
        for offset in range(1, months + 1)
        if _add_months(_add_months(target, -offset), 1) <= today
    ]
    if not analyzed:
        return BudgetSuggestionResponse(
            month=target, months_requested=months, months_analyzed=0, suggestions=[]
        )

    window_start = min(analyzed)
    window_end = _add_months(max(analyzed), 1)

    primary_currency = await _primary_currency(session, user_id)
    viewer = await viewer_currency(session, primary_currency)
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)

    cats_result = await session.execute(
        select(Category, CategoryGroup)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(Category.workspace_id == workspace_id)
    )
    all_categories = cats_result.all()

    net_expr = case(
        (Transaction.type == "debit", line_primary_or_amount(viewer)),
        (Transaction.type == "credit", -line_primary_or_amount(viewer)),
        else_=0,
    )
    window_filters = (
        Transaction.workspace_id == workspace_id,
        report_date >= window_start,
        report_date < window_end,
        report_date <= today,
        line_category_id().isnot(None),
        Transaction.status == "posted",
        counts_as_user_pnl(line_category_id()),
    )

    spending_result = await session.execute(
        with_category_lines(
            select(line_category_id(), func.sum(net_expr)).select_from(Transaction)
        )
        .where(*window_filters)
        .group_by(line_category_id())
    )
    total_by_cat: dict[str, Decimal] = {}
    for cat_uuid, net in spending_result.all():
        net = net or Decimal("0")
        if net != 0:
            total_by_cat[str(cat_uuid)] = net

    # Net per category and day in SQL, rolled up to months in Python (SQLite
    # and Postgres do not share a portable month-truncation function). The
    # "has spending" check runs on the month total, so a debit and an equal
    # refund later in the same month leave that month without data.
    daily_result = await session.execute(
        with_category_lines(
            select(line_category_id(), report_date, func.sum(net_expr)).select_from(Transaction)
        )
        .where(*window_filters)
        .group_by(line_category_id(), report_date)
    )
    monthly_net: dict[tuple[str, date], Decimal] = {}
    for cat_uuid, d, net in daily_result.all():
        key = (str(cat_uuid), d.replace(day=1))
        monthly_net[key] = monthly_net.get(key, Decimal("0")) + (net or Decimal("0"))

    own_offset = await owner_split_offset_by_category(
        session,
        user_id,
        window_start,
        window_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        workspace_id=workspace_id,
    )
    for cat_uuid, total in own_offset.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        if cat_id in total_by_cat:
            total_by_cat[cat_id] -= Decimal(str(total))
            if total_by_cat[cat_id] <= 0:
                total_by_cat.pop(cat_id)

    from app.services._query_filters import (
        _viewer_shared_filters,
        resolve_viewer_category_map,
        viewer_shares_in_primary,
    )

    shared_where = (
        *_viewer_shared_filters(
            user_id,
            window_start,
            window_end,
            use_effective_date=accounting_mode == "accrual",
            exclude_workspace_id=workspace_id,
        ),
        Transaction.type == "debit",
    )
    shared_by_day_cat = await viewer_shares_in_primary(
        session,
        (report_date, Transaction.category_id),
        shared_where,
        primary_currency,
        viewer,
    )
    if shared_by_day_cat:
        shared_cat_ids = {
            cat_uuid for (_d, cat_uuid) in shared_by_day_cat if cat_uuid is not None
        }
        sugg_cat_map = await resolve_viewer_category_map(
            session, shared_cat_ids, workspace_id
        )
        for (d, cat_uuid), (_income, expense) in shared_by_day_cat.items():
            if not expense:
                continue
            mapped_cat = sugg_cat_map.get(cat_uuid) if cat_uuid is not None else None
            if mapped_cat is None:
                continue
            cat_id = str(mapped_cat.id)
            total_by_cat[cat_id] = total_by_cat.get(cat_id, Decimal("0")) + expense
            month_start = d.replace(day=1)
            key = (cat_id, month_start)
            monthly_net[key] = monthly_net.get(key, Decimal("0")) + expense

    months_with_data: dict[str, set] = {}
    for (cat_id, month_start), net in monthly_net.items():
        if net > 0:
            months_with_data.setdefault(cat_id, set()).add(month_start)

    budget_map = await _build_budget_map(session, workspace_id, target)
    divisor = Decimal(len(analyzed))

    suggestions = []
    for category, group in all_categories:
        cat_id = str(category.id)
        total = total_by_cat.get(cat_id)
        if total is None or total <= 0:
            continue
        suggested = (total / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if suggested <= 0:
            continue
        suggestions.append(BudgetSuggestion(
            category_id=category.id,
            category_name=category.name,
            category_icon=category.icon,
            category_color=category.color,
            group_id=group.id if group else None,
            group_name=group.name if group else None,
            suggested_amount=suggested,
            total_spent=total,
            months_with_data=len(months_with_data.get(cat_id, set())),
        ))

    suggestions.sort(key=lambda s: s.suggested_amount, reverse=True)
    for s in suggestions:
        s.existing_budget = str(s.category_id) in budget_map

    return BudgetSuggestionResponse(
        month=target,
        months_requested=months,
        months_analyzed=len(analyzed),
        suggestions=suggestions,
    )


async def create_budgets_batch(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BudgetBatchCreate,
) -> list[Budget]:
    """Create several budgets in one transaction.

    Fails atomically: if any item conflicts with the
    (user, category, month, is_recurring) unique constraint nothing is
    written.
    """
    month = data.month.replace(day=1)
    currency = await _primary_currency(session, user_id)
    seen: set[uuid.UUID] = set()
    budgets: list[Budget] = []
    for item in data.items:
        if item.category_id in seen:
            continue
        seen.add(item.category_id)
        budgets.append(
            Budget(
                user_id=user_id,
                workspace_id=workspace_id,
                category_id=item.category_id,
                amount=item.amount,
                currency=currency,
                month=month,
                is_recurring=data.is_recurring,
                rollover=item.rollover if item.rollover is not None else data.rollover,
                reset_rollover=(
                    item.reset_rollover
                    if item.reset_rollover is not None
                    else data.reset_rollover
                ),
            )
        )
    if not budgets:
        raise ValueError("No budgets to create")

    known = set(
        (
            await session.execute(
                select(Category.id).where(
                    Category.workspace_id == workspace_id,
                    Category.id.in_(seen),
                )
            )
        ).scalars()
    )
    if known != seen:
        raise ValueError("Category not found")

    session.add_all(budgets)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ValueError(
            "A budget for one of these categories already exists for this month"
        )
    for budget in budgets:
        await session.refresh(budget)
    return budgets


async def copy_monthly_budgets(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    source_month: date,
    target_month: date,
    adjustment_percentage: Decimal = Decimal("0.0"),
    overwrite_existing: bool = False,
) -> list[Budget]:
    """Copy the budgets a source month resolves to into a target month.

    The source is read with the same resolution the budget screen uses
    (month override first, then the latest recurring default), across the
    whole workspace, so inherited recurring amounts are copied and adjusted
    too. Only month-specific overrides are written: recurring defaults are
    never created or changed, so the months after the target keep resolving
    the way they did before the copy.

    A category that already has a budget in the target month is left alone
    unless ``overwrite_existing`` is set. When it is, an existing override is
    updated in place (whoever in the workspace created it) instead of adding
    a second row next to it, and a category whose target month already
    resolves to the copied amount is skipped, so it stays on its recurring
    default. Adjusted amounts round half a cent up, as splits do.
    """
    source_first = source_month.replace(day=1)
    target_first = target_month.replace(day=1)
    if source_first == target_first:
        raise ValueError("source_month and target_month must be different months")

    source_map = await _build_budget_map(db, workspace_id, source_first)
    target_map = await _build_budget_map(db, workspace_id, target_first)

    overrides_result = await db.execute(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.month == target_first,
        )
    )
    target_overrides: dict[str, list[Budget]] = {}
    for b in overrides_result.scalars().all():
        target_overrides.setdefault(str(b.category_id), []).append(b)

    multiplier = Decimal("1.0") + (adjustment_percentage / Decimal("100.0"))
    changed: list[Budget] = []

    # A copy keeps the source limit's currency and rollover preference.
    for cat_id, (amount, _is_recurring, currency, rollover, _reset_rollover) in source_map.items():
        new_amount = (amount * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if new_amount > MAX_BUDGET_AMOUNT:
            raise ValueError("Adjusted budget amount is too large")
        if cat_id in target_map and (
            not overwrite_existing
            or (
                target_map[cat_id][0],
                target_map[cat_id][2],
                target_map[cat_id][3],
            )
            == (new_amount, currency, rollover)
        ):
            continue
        overrides = target_overrides.get(cat_id)
        if overrides:
            for override in overrides:
                override.amount = new_amount
                override.currency = currency
                override.rollover = rollover
                override.reset_rollover = False
                changed.append(override)
        else:
            new_budget = Budget(
                workspace_id=workspace_id,
                user_id=user_id,
                category_id=uuid.UUID(cat_id),
                amount=new_amount,
                currency=currency,
                month=target_first,
                is_recurring=False,
                rollover=rollover,
                reset_rollover=False,
            )
            db.add(new_budget)
            changed.append(new_budget)

    await db.commit()
    for b in changed:
        await db.refresh(b)
    return changed


async def get_budget_rollover_summary(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: date,
) -> dict:
    """End-of-month surplus or deficit per budgeted category.

    ``remaining`` is measured against the projected spend (posted spend plus
    the recurring and planned transactions still due that month), so a month
    in progress or in the future isn't reported as mostly unspent.

    Only categories with a positive budget that month are included, in the list
    and in the totals: ``net_surplus`` is the headroom left across the effective
    budget (base + carryover), so spending in unbudgeted categories or cleared
    zero tombstones never turns it into a deficit.
    """
    vs_actuals = await get_budget_vs_actual(db, workspace_id, user_id, month)
    total_budgeted = Decimal("0.00")
    total_carryover = Decimal("0.00")
    total_effective_budget = Decimal("0.00")
    total_spent = Decimal("0.00")
    total_projected = Decimal("0.00")
    categories_summary = []

    for item in vs_actuals:
        if item.budget_amount is None or item.budget_amount <= 0:
            continue
        budgeted = _q(item.budget_amount)
        carryover_in = _q(item.carryover_amount)
        effective_budget = _q(
            item.effective_budget_amount
            if item.effective_budget_amount is not None
            else budgeted + carryover_in
        )
        spent = _q(item.actual_amount)
        projected = _q(item.projected_amount)
        remaining = _q(effective_budget - projected)
        total_budgeted += budgeted
        total_carryover += carryover_in
        total_effective_budget += effective_budget
        total_spent += spent
        total_projected += projected
        categories_summary.append({
            "category_id": item.category_id,
            "category_name": item.category_name,
            "budgeted": budgeted,
            "actual_spent": spent,
            "projected_spent": projected,
            "remaining": remaining,
            "carryover_eligible": remaining > Decimal("0.00"),
            "rollover_enabled": item.rollover,
            "reset_rollover": item.reset_rollover,
            "carryover_in": carryover_in,
            "effective_budget": effective_budget,
        })

    return {
        "month": month.replace(day=1),
        "total_budgeted": _q(total_budgeted),
        "total_spent": _q(total_spent),
        "total_projected_spent": _q(total_projected),
        "net_surplus": _q(total_effective_budget - total_projected),
        "total_carryover": _q(total_carryover),
        "total_effective_budget": _q(total_effective_budget),
        "categories": categories_summary,
    }


async def get_multi_month_forecast(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    start_month: date,
    num_months: int = 6,
) -> dict:
    start = start_month.replace(day=1)
    if num_months <= 0:
        return {"forecast_months": num_months, "items": []}

    cats_result = await db.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    category_by_id = {str(c.id): c for c in cats_result.scalars().all()}
    if not category_by_id:
        items = []
        m_date = start
        for _ in range(num_months):
            items.append({
                "month": m_date,
                "projected_budget": Decimal("0.00"),
                "carryover_balance": Decimal("0.00"),
                "effective_budget": Decimal("0.00"),
                "projected_spend": Decimal("0.00"),
                "projected_variance": Decimal("0.00"),
            })
            m_date = _add_months(m_date, 1)
        return {"forecast_months": num_months, "items": items}

    primary_currency = await _primary_currency(db, user_id)
    viewer = await viewer_currency(db, primary_currency)
    accounting_mode = await get_credit_card_accounting_mode(db)

    end = _add_months(start, num_months)
    horizon_budget_maps = await _resolve_budget_maps_in_range(db, workspace_id, start, end)
    start_budget_map = horizon_budget_maps.get(start, {})

    running_carryover = await _compute_carryover_map(
        db,
        workspace_id,
        user_id,
        start,
        start_budget_map,
        category_by_id,
        primary_currency,
        accounting_mode,
        viewer=viewer,
    )

    active_months = {
        m
        for m, bmap in horizon_budget_maps.items()
        if any(entry[0] > 0 and cat_id in category_by_id for cat_id, entry in bmap.items())
    }
    spending_by_month = await _compute_monthly_spending_map(
        db,
        workspace_id,
        user_id,
        active_months,
        category_by_id,
        primary_currency,
        accounting_mode,
        viewer=viewer,
    )

    items = []
    m_date = start
    for _ in range(num_months):
        bmap = horizon_budget_maps.get(m_date, {})
        m_spend_map = spending_by_month.get(m_date, {})
        total_base = Decimal("0.00")
        total_carryover = Decimal("0.00")
        total_effective = Decimal("0.00")
        total_spend = Decimal("0.00")
        next_carryover: dict[str, Decimal] = {}

        for cat_id, (base, _rec, base_cur, r_rollover, r_reset) in bmap.items():
            if cat_id not in category_by_id or base <= 0:
                continue
            base_q = _q(
                await amount_in_currency(db, base, base_cur, primary_currency, m_date)
            )
            carry_in = (
                Decimal("0.00")
                if (not r_rollover or r_reset)
                else _q(running_carryover.get(cat_id, Decimal("0.00")))
            )
            eff = _q(base_q + carry_in)
            spend = _q(m_spend_map.get(cat_id, Decimal("0.00")))
            variance = _q(eff - spend)
            total_base += base_q
            total_carryover += carry_in
            total_effective += eff
            total_spend += spend
            if r_rollover:
                next_carryover[cat_id] = variance

        running_carryover = next_carryover
        items.append({
            "month": m_date,
            "projected_budget": _q(total_base),
            "carryover_balance": _q(total_carryover),
            "effective_budget": _q(total_effective),
            "projected_spend": _q(total_spend),
            "projected_variance": _q(total_effective - total_spend),
        })
        m_date = _add_months(m_date, 1)

    return {
        "forecast_months": num_months,
        "items": items,
    }
