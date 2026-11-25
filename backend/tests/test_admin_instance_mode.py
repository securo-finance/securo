"""A managed instance keeps its instance settings out of the admin's reach.

ADR 0045: on a hosted instance the operator owns the settings that are
instance-wide (providers, open registration, defaults, timezone). The admin
screen shows them read-only and the API refuses the write; a self-hosted
instance is unchanged.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.app_settings import AppSetting
from app.models.user import User
from app.services import admin_service

pytestmark = pytest.mark.asyncio


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


class TestInstanceModeOff:
    async def test_reports_nothing_locked(self, client: AsyncClient, admin_auth_headers: dict):
        response = await client.get("/api/admin/instance-mode", headers=admin_auth_headers)

        assert response.status_code == 200
        assert response.json() == {
            "managed": False,
            "locked_settings": [],
            "locked_providers": False,
        }

    async def test_writes_still_allowed(
        self, client: AsyncClient, admin_auth_headers: dict, session: AsyncSession
    ):
        response = await client.patch(
            "/api/admin/settings/number_format",
            json={"value": "comma_dot"},
            headers=admin_auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["value"] == "comma_dot"

        await session.execute(delete(AppSetting).where(AppSetting.key == "number_format"))
        await session.commit()


class TestInstanceModeOn:
    async def test_lists_the_locked_settings(
        self, client: AsyncClient, admin_auth_headers: dict, managed
    ):
        response = await client.get("/api/admin/instance-mode", headers=admin_auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["managed"] is True
        assert data["locked_providers"] is True
        assert "registration_enabled" in data["locked_settings"]
        assert "timezone" in data["locked_settings"]

    async def test_setting_write_is_refused(
        self, client: AsyncClient, admin_auth_headers: dict, session: AsyncSession, managed
    ):
        await session.merge(AppSetting(key="registration_enabled", value="true"))
        await session.commit()

        response = await client.patch(
            "/api/admin/settings/registration_enabled",
            json={"value": "false"},
            headers=admin_auth_headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "instance_managed"

        # The value the operator set is still the one in force.
        reading = await client.get(
            "/api/admin/settings/registration_enabled", headers=admin_auth_headers
        )
        assert reading.json()["value"] == "true"

        await session.execute(
            delete(AppSetting).where(AppSetting.key == "registration_enabled")
        )
        await session.commit()

    async def test_setting_delete_is_refused(
        self, client: AsyncClient, admin_auth_headers: dict, managed
    ):
        response = await client.delete(
            "/api/admin/settings/timezone", headers=admin_auth_headers
        )

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "instance_managed"

    async def test_email_template_write_is_refused(
        self, client: AsyncClient, admin_auth_headers: dict, session: AsyncSession, managed
    ):
        # The recovery templates go out from the operator's sender to every
        # user of the instance, so they are the operator's text (ADR 0045).
        response = await client.put(
            "/api/admin/email",
            json={
                "templates": {
                    "en": {
                        "subject": "R1883 tampered",
                        "body": "Your account was locked, call us:\n\n{link}",
                    }
                }
            },
            headers=admin_auth_headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "instance_managed"
        # Nothing reached the database: the operator's text stays in force.
        assert await admin_service.get_email_templates(session) == {}

    async def test_provider_write_is_refused(
        self, client: AsyncClient, admin_auth_headers: dict, managed
    ):
        response = await client.patch(
            "/api/admin/provider-settings/pluggy",
            json={"values": {"client_id": "anything"}},
            headers=admin_auth_headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "instance_managed"

    async def test_reading_providers_still_works(
        self, client: AsyncClient, admin_auth_headers: dict, managed
    ):
        response = await client.get(
            "/api/admin/provider-settings", headers=admin_auth_headers
        )

        assert response.status_code == 200

    async def test_unknown_setting_is_still_a_bad_request(
        self, client: AsyncClient, admin_auth_headers: dict, managed
    ):
        response = await client.patch(
            "/api/admin/settings/not_a_setting",
            json={"value": "x"},
            headers=admin_auth_headers,
        )

        assert response.status_code == 400

    async def test_a_non_admin_cannot_read_the_mode(
        self, client: AsyncClient, viewer_auth_headers: dict, managed
    ):
        response = await client.get(
            "/api/admin/instance-mode", headers=viewer_auth_headers
        )

        assert response.status_code == 403

    async def test_other_admin_screens_still_work(
        self, client: AsyncClient, admin_auth_headers: dict, test_user: User, managed
    ):
        # Locking instance settings is not locking the admin out of the screen:
        # users, accounting mode and the theme stay readable.
        assert (
            await client.get("/api/admin/users", headers=admin_auth_headers)
        ).status_code == 200
        assert (
            await client.get("/api/admin/default-colors", headers=admin_auth_headers)
        ).status_code == 200
