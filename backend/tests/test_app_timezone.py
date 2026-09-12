from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.models.app_settings import AppSetting


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc).astimezone(tz)


@pytest.mark.asyncio
async def test_saved_timezone_takes_effect_for_new_operations(session, clean_db):
    from app.core.app_clock import app_today, use_timezone

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()

    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"

            setting.value = "Asia/Tokyo"
            await session.commit()
            assert app_today().isoformat() == "2026-05-18"

        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-19"


@pytest.mark.asyncio
async def test_tz_environment_falls_back_to_system_timezone(session, clean_db, monkeypatch):
    from app.core.app_clock import app_today, use_timezone

    monkeypatch.setenv("TZ", "Unavailable/Zone")
    with (
        patch("tzlocal.get_localzone", return_value=ZoneInfo("America/Bahia")),
        patch("app.core.app_clock.datetime", FixedDatetime),
    ):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"


@pytest.mark.asyncio
async def test_invalid_saved_timezone_keeps_repair_endpoint_available(
    client, admin_auth_headers, session, monkeypatch, caplog
):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    session.add(AppSetting(key="timezone", value="Unavailable/Zone"))
    await session.commit()

    response = await client.get("/api/setup/status")
    assert response.status_code == 200

    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Saved application timezone is unavailable" in caplog.text

    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "UTC"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_set_timezone_and_invalid_names_are_rejected(
    client, admin_auth_headers
):
    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "America/Sao_Paulo"},
    )
    assert response.status_code == 200

    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Europe/London" in response.json()["available"]

    for value in ("Invalid/Timezone", "../UTC", ""):
        response = await client.patch(
            "/api/admin/settings/timezone",
            headers=admin_auth_headers,
            json={"value": value},
        )
        assert response.status_code == 400
