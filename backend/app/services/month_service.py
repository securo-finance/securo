import uuid
from datetime import date
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_period import MonthlyPeriod
from app.models.monthly_snapshot import MonthlySnapshot
from app.models.user import User
from app.schemas.month import (
    CloseCurrentMonthRead,
    ClosedMonthSnapshotRead,
    CurrentMonthRead,
    normalize_period_value,
)

CURRENT_MONTH_PREFERENCE_KEY = "current_month_period"
SELECTED_SNAPSHOT_PREFERENCE_KEY = "selected_snapshot_period"


def get_current_month_period(user: User) -> str | None:
    preferences = user.preferences or {}
    period = preferences.get(CURRENT_MONTH_PREFERENCE_KEY)
    if not period:
        return None
    try:
        return normalize_period_value(str(period))
    except ValueError:
        return None


def get_selected_snapshot_period(user: User) -> str | None:
    preferences = user.preferences or {}
    period = preferences.get(SELECTED_SNAPSHOT_PREFERENCE_KEY)
    if not period:
        return None
    try:
        return normalize_period_value(str(period))
    except ValueError:
        return None


def get_selected_view_period(user: User) -> str | None:
    return get_selected_snapshot_period(user) or get_current_month_period(user)


def is_snapshot_view(user: User) -> bool:
    return get_selected_snapshot_period(user) is not None


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


def get_current_month_state_sync(user: User, snapshots: list[ClosedMonthSnapshotRead] | None = None) -> CurrentMonthRead:
    period = get_current_month_period(user)
    selected_snapshot_period = get_selected_snapshot_period(user)
    selected_period = selected_snapshot_period or period
    return CurrentMonthRead(
        current_period=period,
        current_period_label=format_period_label(period),
        is_defined=period is not None,
        selected_mode="snapshot" if selected_snapshot_period else "current",
        selected_period=selected_period,
        selected_period_label=format_period_label(selected_period),
        is_snapshot_view=selected_snapshot_period is not None,
        snapshots=snapshots or [],
    )


def ensure_current_month_defined(user: User) -> None:
    if get_current_month_period(user) is None:
        raise ValueError(
            "Mês Atual não definido. Defina o período do mês atual antes de sincronizar ou criar dados manualmente."
        )


def ensure_not_snapshot_view(user: User) -> None:
    if is_snapshot_view(user):
        raise ValueError(
            "Você está visualizando um mês fechado. Volte ao Mês Atual para sincronizar ou editar dados financeiros."
        )


def ensure_current_month_editable(user: User) -> None:
    ensure_current_month_defined(user)
    ensure_not_snapshot_view(user)


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


async def get_monthly_snapshot(
    session: AsyncSession,
    user_id: uuid.UUID,
    period: str,
) -> MonthlySnapshot | None:
    normalized_period = normalize_period_value(period)
    result = await session.execute(
        select(MonthlySnapshot).where(
            MonthlySnapshot.user_id == user_id,
            MonthlySnapshot.period == normalized_period,
        )
    )
    return result.scalar_one_or_none()


async def list_monthly_snapshots(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[ClosedMonthSnapshotRead]:
    result = await session.execute(
        select(MonthlySnapshot)
        .where(MonthlySnapshot.user_id == user_id)
        .order_by(MonthlySnapshot.period.desc())
    )
    snapshots = list(result.scalars().all())
    return [
        ClosedMonthSnapshotRead(
            period=snapshot.period,
            period_label=format_period_label(snapshot.period) or snapshot.period,
            closed_at=snapshot.closed_at.astimezone(timezone.utc).isoformat(),
        )
        for snapshot in snapshots
    ]


async def get_current_month_state(
    session: AsyncSession,
    user: User,
) -> CurrentMonthRead:
    return get_current_month_state_sync(user, await list_monthly_snapshots(session, user.id))


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


async def resolve_selected_view_monthly_period(
    session: AsyncSession,
    user_id: uuid.UUID,
    user: User | None = None,
) -> MonthlyPeriod | None:
    if user is None:
        user = await session.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    selected_period = get_selected_view_period(user)
    if selected_period is None:
        return None

    current_period = get_current_month_period(user)
    if current_period == selected_period:
        return await resolve_current_monthly_period(session, user_id, user)
    return await get_monthly_period(session, user_id, selected_period)


async def set_current_month_period(
    session: AsyncSession,
    user_id,
    existing_preferences: dict | None,
    period: str,
) -> CurrentMonthRead:
    normalized_period = normalize_period_value(period)
    if await get_monthly_snapshot(session, user_id, normalized_period) is not None:
        raise ValueError("Esse mês já foi fechado e não pode voltar a ser o Mês Atual editável.")

    preferences = dict(existing_preferences or {})
    preferences[CURRENT_MONTH_PREFERENCE_KEY] = normalized_period
    preferences.pop(SELECTED_SNAPSHOT_PREFERENCE_KEY, None)

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
    return await get_current_month_state(session, updated_user)


async def set_month_view(
    session: AsyncSession,
    user_id,
    existing_preferences: dict | None,
    mode: str,
    period: str | None = None,
) -> CurrentMonthRead:
    preferences = dict(existing_preferences or {})
    if mode == "snapshot":
        if not period:
            raise ValueError("Snapshot view requires a period")
        normalized_period = normalize_period_value(period)
        snapshot = await get_monthly_snapshot(session, user_id, normalized_period)
        if snapshot is None:
            raise ValueError("Closed snapshot not found")
        preferences[SELECTED_SNAPSHOT_PREFERENCE_KEY] = normalized_period
    else:
        preferences.pop(SELECTED_SNAPSHOT_PREFERENCE_KEY, None)

    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(preferences=preferences)
    )
    await session.commit()

    updated_user = await session.get(User, user_id)
    if updated_user is None:
        raise ValueError("User not found")
    return await get_current_month_state(session, updated_user)


async def close_current_month(
    session: AsyncSession,
    user_id: uuid.UUID,
    user: User | None,
    next_period: str,
) -> CloseCurrentMonthRead:
    if user is None:
        user = await session.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    ensure_current_month_defined(user)
    current_period = get_current_month_period(user)
    if current_period is None:
        raise ValueError("Mês Atual não definido")

    normalized_next_period = normalize_period_value(next_period)
    if normalized_next_period == current_period:
        raise ValueError("O próximo Mês Atual deve ser diferente do mês que está sendo fechado.")
    if await get_monthly_snapshot(session, user_id, current_period) is not None:
        raise ValueError("Esse mês já foi fechado.")
    if await get_monthly_snapshot(session, user_id, normalized_next_period) is not None:
        raise ValueError("O próximo Mês Atual não pode usar um mês já fechado.")

    current_monthly_period = await resolve_current_monthly_period(session, user_id, user)
    snapshot = MonthlySnapshot(
        user_id=user_id,
        monthly_period_id=current_monthly_period.id,
        period=current_period,
        closed_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)

    await get_or_create_monthly_period(session, user_id, normalized_next_period)

    preferences = dict(user.preferences or {})
    preferences[CURRENT_MONTH_PREFERENCE_KEY] = normalized_next_period
    preferences.pop(SELECTED_SNAPSHOT_PREFERENCE_KEY, None)

    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(preferences=preferences)
    )
    await session.commit()

    updated_user = await session.get(User, user_id)
    if updated_user is None:
        raise ValueError("User not found")

    return CloseCurrentMonthRead(
        state=await get_current_month_state(session, updated_user),
        closed_snapshot=ClosedMonthSnapshotRead(
            period=current_period,
            period_label=format_period_label(current_period) or current_period,
            closed_at=snapshot.closed_at.astimezone(timezone.utc).isoformat(),
        ),
    )
