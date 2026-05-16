import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.goal import Goal
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalRead, GoalSummary, GoalUpdate
from app.services.asset_service import _compute_current_value, _get_latest_value, get_asset_values_at
from app.services.dashboard_service import _account_balance_at, _get_open_accounts
from app.services.account_service import get_account_name
from app.services.fx_rate_service import convert


async def _get_primary_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def _resolve_current_amount(
    session: AsyncSession, goal: Goal, user_id: uuid.UUID
) -> Decimal:
    """Resolve the current_amount based on tracking_type.

    Returns the amount in the goal's currency.
    """
    goal_currency = goal.currency

    if goal.tracking_type == "account" and goal.account_id:
        account = await session.get(Account, goal.account_id)
        if account:
            # Use dashboard's balance logic so manual accounts are computed correctly
            bal = Decimal(str(await _account_balance_at(session, account, date.today())))
            if account.currency != goal_currency:
                converted, _ = await convert(
                    session, bal, account.currency, goal_currency
                )
                return converted
            return bal
        return goal.current_amount
    elif goal.tracking_type == "asset" and goal.asset_id:
        asset = await session.get(Asset, goal.asset_id)
        if asset:
            latest = await _get_latest_value(session, asset.id)
            value = _compute_current_value(asset, latest)
            if value is not None:
                bal = Decimal(str(value))
                if asset.currency != goal_currency:
                    converted, _ = await convert(
                        session, bal, asset.currency, goal_currency
                    )
                    return converted
                return bal
        return goal.current_amount
    elif goal.tracking_type == "net_worth":
        # Reuse dashboard's account query and balance logic so manual accounts
        # (whose balance is computed from transactions) are handled correctly.
        accounts = await _get_open_accounts(session, user_id)
        today = date.today()
        total = Decimal("0")
        for acc in accounts:
            bal = Decimal(str(await _account_balance_at(session, acc, today)))
            if acc.currency == goal_currency:
                total += bal
            else:
                converted, _ = await convert(session, bal, acc.currency, goal_currency)
                total += converted

        # Add asset values
        assets_by_currency, _ = await get_asset_values_at(session, user_id)
        for currency, amount in assets_by_currency.items():
            if currency == goal_currency:
                total += Decimal(str(amount))
            else:
                converted, _ = await convert(session, Decimal(str(amount)), currency, goal_currency)
                total += converted

        return total
    else:
        return goal.current_amount


def _compute_percentage(current: Decimal, target: Decimal) -> float:
    if target <= 0:
        return 100.0 if current > 0 else 0.0
    return round(float(current / target) * 100, 1)


def _compute_monthly_contribution(
    current: Decimal, target: Decimal, target_date: Optional[date]
) -> Optional[float]:
    if not target_date:
        return None
    today = date.today()
    if today >= target_date:
        return 0.0
    remaining = target - current
    if remaining <= 0:
        return 0.0
    # Calculate months remaining (approximate)
    months = (target_date.year - today.year) * 12 + (target_date.month - today.month)
    if months <= 0:
        months = 1
    return round(float(remaining / months), 2)


def _compute_on_track(
    current: Decimal, target: Decimal, target_date: Optional[date],
    created_at: Optional[date] = None, initial_amount: Decimal = Decimal("0"),
) -> Optional[str]:
    if not target_date:
        return None
    if current >= target:
        return "achieved"
    today = date.today()
    if today > target_date:
        return "overdue"

    # Use created_at as start date; fall back to today (no progress expected yet)
    start = created_at if created_at else today
    total_days = (target_date - start).days
    if total_days <= 0:
        return "on_track"

    # Measure progress relative to the starting baseline, not from zero.
    # total_needed = how much needs to be saved from initial to target
    # actual_progress = how much has been saved since creation
    total_needed = target - initial_amount
    if total_needed <= 0:
        return "achieved"

    elapsed_days = (today - start).days
    expected_progress = total_needed * Decimal(str(elapsed_days / total_days))
    actual_progress = current - initial_amount

    diff = actual_progress - expected_progress
    # Express tolerance as percentage of total_needed
    tolerance = total_needed * Decimal("0.05")

    if diff >= tolerance * 2:
        return "ahead"
    if diff >= -tolerance:
        return "on_track"
    return "behind"


async def _enrich_goal(
    session: AsyncSession, goal: Goal, user_id: uuid.UUID
) -> GoalRead:
    """Enrich a goal with computed fields."""
    current = await _resolve_current_amount(session, goal, user_id)
    percentage = _compute_percentage(current, goal.target_amount)
    monthly = _compute_monthly_contribution(current, goal.target_amount, goal.target_date)
    goal_start = goal.created_at.date() if goal.created_at else None
    on_track = _compute_on_track(
        current, goal.target_amount, goal.target_date, goal_start, goal.initial_amount
    )

    # Get linked account/asset name
    account_name = None
    if goal.account_id:
        account = await session.get(Account, goal.account_id)
        if account:
            account_name = get_account_name(account)
    asset_name = None
    if goal.asset_id:
        asset = await session.get(Asset, goal.asset_id)
        if asset:
            asset_name = asset.name

    # Convert to primary currency if needed
    primary_currency = await _get_primary_currency(session, user_id)
    target_primary = None
    current_primary = None
    if goal.currency != primary_currency:
        target_converted, _ = await convert(
            session, goal.target_amount, goal.currency, primary_currency
        )
        current_converted, _ = await convert(
            session, current, goal.currency, primary_currency
        )
        target_primary = target_converted
        current_primary = current_converted

    return GoalRead(
        id=goal.id,
        user_id=goal.user_id,
        name=goal.name,
        target_amount=goal.target_amount,
        current_amount=current,
        currency=goal.currency,
        target_amount_primary=target_primary,
        current_amount_primary=current_primary,
        target_date=goal.target_date,
        tracking_type=goal.tracking_type,
        account_id=goal.account_id,
        asset_id=goal.asset_id,
        status=goal.status,
        icon=goal.icon,
        color=goal.color,
        position=goal.position,
        metadata_json=goal.metadata_json,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        percentage=percentage,
        monthly_contribution=monthly,
        on_track=on_track,
        account_name=account_name,
        asset_name=asset_name,
    )


async def get_goals(
    session: AsyncSession, user_id: uuid.UUID, status: Optional[str] = None
) -> list[GoalRead]:
    query = select(Goal).where(Goal.user_id == user_id).order_by(Goal.position, Goal.created_at)
    if status:
        query = query.where(Goal.status == status)
    result = await session.execute(query)
    goals = list(result.scalars().all())
    return [await _enrich_goal(session, g, user_id) for g in goals]


async def get_goal(
    session: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[GoalRead]:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return None
    return await _enrich_goal(session, goal, user_id)


async def create_goal(
    session: AsyncSession, user_id: uuid.UUID, data: GoalCreate
) -> GoalRead:
    goal = Goal(
        user_id=user_id,
        name=data.name,
        target_amount=data.target_amount,
        current_amount=data.current_amount,
        currency=data.currency,
        target_date=data.target_date,
        tracking_type=data.tracking_type,
        account_id=data.account_id,
        asset_id=data.asset_id,
        icon=data.icon,
        color=data.color,
        metadata_json=data.metadata_json,
    )
    # Capture the starting balance so on-track logic measures progress from baseline
    initial = await _resolve_current_amount(session, goal, user_id)
    goal.initial_amount = initial
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def update_goal(
    session: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID, data: GoalUpdate
) -> Optional[GoalRead]:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(goal, field, value)
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def delete_goal(
    session: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return False
    await session.delete(goal)
    await session.commit()
    return True


async def get_goal_summary(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 3
) -> list[GoalSummary]:
    """Get a summary of active goals for the dashboard widget."""
    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user_id, Goal.status == "active")
        .order_by(Goal.position, Goal.created_at)
        .limit(limit)
    )
    goals = list(result.scalars().all())
    summaries = []
    for goal in goals:
        current = await _resolve_current_amount(session, goal, user_id)
        percentage = _compute_percentage(current, goal.target_amount)
        monthly = _compute_monthly_contribution(current, goal.target_amount, goal.target_date)
        goal_start = goal.created_at.date() if goal.created_at else None
        on_track = _compute_on_track(
            current, goal.target_amount, goal.target_date, goal_start, goal.initial_amount
        )
        summaries.append(GoalSummary(
            id=goal.id,
            name=goal.name,
            target_amount=goal.target_amount,
            current_amount=current,
            currency=goal.currency,
            target_date=goal.target_date,
            status=goal.status,
            icon=goal.icon,
            color=goal.color,
            percentage=percentage,
            monthly_contribution=monthly,
            on_track=on_track,
        ))
    return summaries
