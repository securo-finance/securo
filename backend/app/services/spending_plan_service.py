"""The daily spending plan: one allowance for every variable expense.

Fixed costs (recurring bills) and planned costs (future-dated transactions)
already sit in the balance projection. The plan spends a daily allowance on
every day from today on, and answers three questions: how much is left to
spend today, how much a day the balance can afford, and until when the
balance stays above the floor.
"""

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.spending_plan import SpendingPlan
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.spending_plan import (
    SimulatedPurchase,
    SpendingPlanPoint,
    SpendingPlanTimeline,
    SpendingPlanToday,
    SpendingPlanWrite,
)
from app.services.balance_projection import (
    covered_until,
    load_projection_base,
    positive_primary_amount,
    safe_daily_allowance,
    walk,
)
from app.services.dashboard_service import _get_open_accounts
from app.services.loan_schedule import add_months


async def get_plan(session: AsyncSession, workspace_id: uuid.UUID) -> Optional[SpendingPlan]:
    result = await session.execute(
        select(SpendingPlan).where(SpendingPlan.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def save_plan(
    session: AsyncSession, workspace_id: uuid.UUID, data: SpendingPlanWrite
) -> SpendingPlan:
    plan = await get_plan(session, workspace_id)
    if plan is None:
        plan = SpendingPlan(workspace_id=workspace_id)
        session.add(plan)
    plan.daily_allowance = data.daily_allowance
    plan.balance_floor = data.balance_floor
    plan.account_ids = (
        [str(account_id) for account_id in data.account_ids]
        if data.account_ids is not None
        else None
    )
    plan.horizon_months = data.horizon_months
    await session.commit()
    await session.refresh(plan)
    return plan


async def delete_plan(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    plan = await get_plan(session, workspace_id)
    if plan is not None:
        await session.delete(plan)
        await session.commit()


async def plan_account_ids(
    session: AsyncSession, workspace_id: uuid.UUID, plan: Optional[SpendingPlan]
) -> list[uuid.UUID]:
    """The open accounts the plan spends from. By default every one but loans:
    a loan is long-term debt, not money the allowance can spend."""
    chosen = (
        [uuid.UUID(account_id) for account_id in plan.account_ids]
        if plan is not None and plan.account_ids is not None
        else None
    )
    if chosen is not None and not chosen:
        return []
    accounts = await _get_open_accounts(session, workspace_id, chosen)
    return [account.id for account in accounts if chosen is not None or account.type != "loan"]


async def variable_spent_on(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    day: date,
    account_ids: Optional[list[uuid.UUID]],
) -> float:
    """Variable spending recorded on ``day``: the expenses the allowance pays for.

    Bills linked to a recurring are fixed costs the projection already holds;
    transfers, ignored rows and refunds are not spending.
    """
    if account_ids is not None and not account_ids:
        return 0.0
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,  # noqa: E712
            Transaction.date == day,
            Transaction.type == "debit",
            Transaction.is_ignored == False,  # noqa: E712
            Transaction.exclude_from_pnl == False,  # noqa: E712
            Transaction.recurring_transaction_id.is_(None),
            Transaction.transfer_pair_id.is_(None),
            Transaction.source.notin_(["transfer", "opening_balance"]),
            or_(
                Transaction.category_id.is_(None),
                (Category.is_ignored == False) & (Category.treat_as_transfer == False),  # noqa: E712
            ),
        )
        .options(selectinload(Transaction.account))
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    total = 0.0
    for tx in (await session.execute(stmt)).scalars():
        total += _spending_share(tx) * await positive_primary_amount(
            session, tx.amount, tx.currency, primary_currency, tx.amount_primary
        )
    return round(total, 2)


def _spending_share(tx: Transaction) -> float:
    """The part of a row split across categories that is spending: its
    transfer-like and ignored parts are not."""
    total = abs(float(tx.amount or 0))
    if not tx.has_category_lines or not tx.category_lines or not total:
        return 1.0
    spent = sum(
        float(line.amount)
        for line in tx.category_lines
        if not (line.category and (line.category.is_ignored or line.category.treat_as_transfer))
    )
    return spent / total


async def calendar_allowance(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    account_ids: Optional[list[uuid.UUID]],
) -> Optional[tuple[float, float]]:
    """The allowance and today's variable spending the calendar should walk with,
    or None when the plan does not apply to the calendar's accounts.

    The allowance spends the plan's accounts, so the calendar applies it only
    when it shows all accounts or exactly the plan's accounts. A calendar
    filtered to, say, the savings account must not spend groceries from it.
    """
    plan = await get_plan(session, workspace_id)
    if plan is None:
        return None
    scope = await plan_account_ids(session, workspace_id, plan)
    if account_ids is not None and set(account_ids) != set(scope):
        return None
    spent = await variable_spent_on(session, workspace_id, primary_currency, app_today(), scope)
    return float(plan.daily_allowance), spent


def purchase_installments(
    purchases: Sequence[SimulatedPurchase], today: date
) -> list[tuple[date, float]]:
    """Split each simulated purchase into monthly installments.

    The cents that do not divide evenly go to the first installment, the way
    card issuers usually charge them. An installment dated before today lands
    today: the simulation asks about the money ahead, not the past.
    """
    charges: list[tuple[date, float]] = []
    for purchase in purchases:
        total_cents = int((purchase.amount * 100).to_integral_value())
        share, leftover = divmod(total_cents, purchase.installments)
        for number in range(purchase.installments):
            cents = share + (leftover if number == 0 else 0)
            day = max(add_months(purchase.first_date, number), today)
            charges.append((day, cents / 100))
    return charges


async def get_timeline(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    daily_allowance: Optional[Decimal] = None,
    purchases: Sequence[SimulatedPurchase] = (),
) -> SpendingPlanTimeline:
    """Project the plan's accounts day by day through the plan's horizon.

    ``daily_allowance`` previews another allowance and ``purchases`` previews
    purchases, both without saving anything. Without a saved plan the
    timeline still answers, with no allowance and the default horizon, so
    the setup screen can suggest a safe value.
    """
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    plan = await get_plan(session, workspace_id)
    allowance = float(
        daily_allowance
        if daily_allowance is not None
        else (plan.daily_allowance if plan is not None else 0)
    )
    floor = float(plan.balance_floor) if plan is not None else 0.0
    horizon_months = plan.horizon_months if plan is not None else 12

    today = app_today()
    horizon_end = add_months(today, horizon_months)
    scope = await plan_account_ids(session, workspace_id, plan)
    base = await load_projection_base(
        session, workspace_id, primary_currency, today, horizon_end + timedelta(days=1), scope
    )
    if purchases:
        deltas = dict(base.deltas)
        for day, amount in purchase_installments(purchases, today):
            deltas[day] = deltas.get(day, 0.0) - amount
        base = replace(base, deltas=deltas)
    spent = await variable_spent_on(session, workspace_id, primary_currency, today, scope)
    points = walk(base, today, allowance, spent)
    last_covered = covered_until(points, today, floor)
    untouched = walk(base, today, 0.0, spent)
    average_surplus = (untouched[-1].balance - untouched[0].balance) / max(1, len(untouched) - 1)

    return SpendingPlanTimeline(
        currency=primary_currency,
        daily_allowance=allowance,
        balance_floor=floor,
        horizon_end=horizon_end,
        safe_daily_allowance=safe_daily_allowance(base, today, floor, spent),
        average_daily_surplus=round(average_surplus, 2),
        covered_until=last_covered,
        covered_through_horizon=last_covered == horizon_end,
        today=SpendingPlanToday(
            allowance=allowance,
            spent=spent,
            remaining=round(allowance - spent, 2),
        ),
        points=[SpendingPlanPoint(date=p.date, balance=p.balance) for p in points],
    )
