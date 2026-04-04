import pytest
from datetime import date, datetime, timezone
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.monthly_period import MonthlyPeriod
from app.models.monthly_snapshot import MonthlySnapshot
from app.models.user import User


@pytest.mark.asyncio
async def test_get_current_month_state(client: AsyncClient, auth_headers):
    response = await client.get("/api/months/current", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["is_defined"] is True
    assert data["current_period"].count("-") == 1
    assert data["current_period_label"].count("/") == 1
    assert data["selected_mode"] == "current"
    assert data["selected_period"] == data["current_period"]
    assert data["selected_period_label"] == data["current_period_label"]
    assert data["is_snapshot_view"] is False
    assert data["snapshots"] == []


@pytest.mark.asyncio
async def test_set_current_month_accepts_month_input(
    client: AsyncClient, auth_headers, session
):
    response = await client.put(
        "/api/months/current",
        headers=auth_headers,
        json={"period": "2026-04"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_period"] == "2026-04"
    assert data["current_period_label"] == "04/2026"
    assert data["is_defined"] is True

    stored_period = await session.scalar(
        select(MonthlyPeriod).where(MonthlyPeriod.period == "2026-04")
    )
    assert stored_period is not None


@pytest.mark.asyncio
async def test_set_current_month_accepts_competency_format(
    client: AsyncClient, auth_headers
):
    response = await client.put(
        "/api/months/current",
        headers=auth_headers,
        json={"period": "05/2026"},
    )

    assert response.status_code == 200
    assert response.json()["current_period"] == "2026-05"


@pytest.mark.asyncio
async def test_get_current_month_returns_undefined_when_missing(
    client: AsyncClient, auth_headers, session
):
    await session.execute(
        update(User)
        .values(
            preferences={
                "language": "pt-BR",
                "date_format": "DD/MM/YYYY",
                "timezone": "America/Sao_Paulo",
                "currency_display": "BRL",
            }
        )
    )
    await session.commit()

    response = await client.get("/api/months/current", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "current_period": None,
        "current_period_label": None,
        "is_defined": False,
        "selected_mode": "current",
        "selected_period": None,
        "selected_period_label": None,
        "is_snapshot_view": False,
        "snapshots": [],
    }


@pytest.mark.asyncio
async def test_close_current_month_creates_snapshot_and_bootstraps_next_period(
    client: AsyncClient, auth_headers, session
):
    current_period = date.today().replace(day=1).isoformat()[:7]
    next_period = "2026-05" if current_period != "2026-05" else "2026-06"

    response = await client.post(
        "/api/months/close",
        headers=auth_headers,
        json={"next_period": next_period},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["closed_snapshot"]["period"] == current_period
    assert data["state"]["current_period"] == next_period
    assert data["state"]["selected_mode"] == "current"
    assert data["state"]["selected_period"] == next_period
    assert data["state"]["snapshots"][0]["period"] == current_period

    snapshot = await session.scalar(
        select(MonthlySnapshot).where(MonthlySnapshot.period == current_period)
    )
    assert snapshot is not None


@pytest.mark.asyncio
async def test_set_month_view_switches_to_closed_snapshot(
    client: AsyncClient, auth_headers, session, test_user, test_monthly_period
):
    current_period = date.today().replace(day=1).isoformat()[:7]
    next_period = "2026-05" if current_period != "2026-05" else "2026-06"
    monthly_period = test_monthly_period

    session.add(
        MonthlySnapshot(
            user_id=test_user.id,
            monthly_period_id=monthly_period.id,
            period=current_period,
            closed_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    await session.execute(
        update(User)
        .where(User.id == test_user.id)
        .values(
            preferences={
                **(test_user.preferences or {}),
                "current_month_period": next_period,
            }
        )
    )
    await session.commit()

    response = await client.put(
        "/api/months/view",
        headers=auth_headers,
        json={"mode": "snapshot", "period": current_period},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["selected_mode"] == "snapshot"
    assert data["selected_period"] == current_period
    assert data["is_snapshot_view"] is True
    assert data["snapshots"][0]["period"] == current_period


@pytest.mark.asyncio
async def test_set_month_view_supports_snapshot_browsing_without_current_month(
    client: AsyncClient, auth_headers, session, test_user, test_monthly_period
):
    current_period = (test_user.preferences or {})["current_month_period"]
    session.add(
        MonthlySnapshot(
            user_id=test_user.id,
            monthly_period_id=test_monthly_period.id,
            period=current_period,
            closed_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    await session.execute(
        update(User)
        .where(User.id == test_user.id)
        .values(
            preferences={
                "language": "pt-BR",
                "date_format": "DD/MM/YYYY",
                "timezone": "America/Sao_Paulo",
                "currency_display": "BRL",
            }
        )
    )
    await session.commit()

    response = await client.put(
        "/api/months/view",
        headers=auth_headers,
        json={"mode": "snapshot", "period": current_period},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_period"] is None
    assert data["is_defined"] is False
    assert data["selected_mode"] == "snapshot"
    assert data["selected_period"] == current_period
    assert data["is_snapshot_view"] is True
