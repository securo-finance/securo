import uuid
from datetime import date

from sqlalchemy import update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_period import MonthlyPeriod
from app.models.user import User
from app.schemas.month import CurrentMonthRead, normalize_period_value

CURRENT_MONTH_PREFERENCE_KEY = "current_month_period"


def get_current_month_period(user: User) -> str | None:
    preferences = user.preferences or {}
    period = preferences.get(CURRENT_MONTH_PREFERENCE_KEY)
    if not period:
        return None
    try:
        return normalize_period_value(str(period))
    except ValueError:
        return None


def format_period_label(period: str | None) -> str | None:
    if not period:
        return None
    year, month = period.split("-")
    return f"{month}/{year}"


def period_to_month_start(period: str) -> date:
    normalized = normalize_period_value(period)
    year, month = normalized.split("-")
    return date(int(year), int(month), 1)


def month_to_period_value(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def get_current_month_state(user: User) -> CurrentMonthRead:
    period = get_current_month_period(user)
    return CurrentMonthRead(
        current_period=period,
        current_period_label=format_period_label(period),
        is_defined=period is not None,
    )


def ensure_current_month_defined(user: User) -> None:
    if get_current_month_period(user) is None:
        raise ValueError(
            "Mês Atual não definido. Defina o período do mês atual antes de sincronizar ou criar dados manualmente."
        )


async def get_monthly_period(
    session: AsyncSession,
    user_id: uuid.UUID,
    period: str,
) -> MonthlyPeriod | None:
    normalized_period = normalize_period_value(period)
    result = await session.execute(
        select(MonthlyPeriod).where(
            MonthlyPeriod.user_id == user_id,
            MonthlyPeriod.period == normalized_period,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_monthly_period(
    session: AsyncSession,
    user_id: uuid.UUID,
    period: str,
) -> MonthlyPeriod:
    normalized_period = normalize_period_value(period)
    monthly_period = await get_monthly_period(session, user_id, normalized_period)
    if monthly_period is not None:
        return monthly_period

    monthly_period = MonthlyPeriod(user_id=user_id, period=normalized_period)
    session.add(monthly_period)
    await session.flush()
    return monthly_period


async def resolve_current_monthly_period(
    session: AsyncSession,
    user_id: uuid.UUID,
    user: User | None = None,
) -> MonthlyPeriod:
    if user is None:
        user = await session.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    ensure_current_month_defined(user)
    current_period = get_current_month_period(user)
    if current_period is None:
        raise ValueError("Mês Atual não definido")

    return await get_or_create_monthly_period(session, user_id, current_period)


async def set_current_month_period(
    session: AsyncSession,
    user_id,
    existing_preferences: dict | None,
    period: str,
) -> CurrentMonthRead:
    normalized_period = normalize_period_value(period)
    preferences = dict(existing_preferences or {})
    preferences[CURRENT_MONTH_PREFERENCE_KEY] = normalized_period

    await get_or_create_monthly_period(session, user_id, normalized_period)

    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(preferences=preferences)
    )
    await session.commit()

    updated_user = await session.get(User, user_id)
    if updated_user is None:
        raise ValueError("User not found")
    return get_current_month_state(updated_user)
