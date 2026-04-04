from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

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


async def set_current_month_period(
    session: AsyncSession,
    user_id,
    existing_preferences: dict | None,
    period: str,
) -> CurrentMonthRead:
    normalized_period = normalize_period_value(period)
    preferences = dict(existing_preferences or {})
    preferences[CURRENT_MONTH_PREFERENCE_KEY] = normalized_period

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
