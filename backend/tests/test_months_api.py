import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.monthly_period import MonthlyPeriod
from app.models.user import User


@pytest.mark.asyncio
async def test_get_current_month_state(client: AsyncClient, auth_headers):
    response = await client.get("/api/months/current", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["is_defined"] is True
    assert data["current_period"].count("-") == 1
    assert data["current_period_label"].count("/") == 1


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
    }
