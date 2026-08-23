from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_settings import AppSetting
from app.models.user import User


@pytest.mark.asyncio
async def test_admin_can_persist_valid_app_timezone(
    client: AsyncClient, admin_auth_headers: dict, test_superuser: User
):
    response = await client.patch(
        "/api/admin/settings/app_timezone",
        json={"value": "America/Sao_Paulo"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["key"] == "app_timezone"
    assert response.json()["value"] == "America/Sao_Paulo"

    current = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert current.status_code == 200
    assert current.json() == {"timezone": "America/Sao_Paulo"}


@pytest.mark.asyncio
async def test_invalid_app_timezone_is_rejected(
    client: AsyncClient, admin_auth_headers: dict, test_superuser: User
):
    response = await client.patch(
        "/api/admin/settings/app_timezone",
        json={"value": "Mars/Phobos"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_app_timezone_defaults_to_utc_for_signed_in_users(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    response = await client.get("/api/admin/timezone", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"timezone": "UTC"}


@pytest.mark.asyncio
async def test_context_primer_uses_configured_app_timezone_date(
    session: AsyncSession, test_user: User
):
    from app.agents.services.context_service import build_context_primer

    session.add(AppSetting(key="app_timezone", value="America/Sao_Paulo"))
    await session.commit()

    primer = await build_context_primer(
        session,
        test_user,
        now=datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc),
    )

    assert "- Timezone: America/Sao_Paulo" in primer
    assert "- Today is 2026-05-18 (America/Sao_Paulo)" in primer
