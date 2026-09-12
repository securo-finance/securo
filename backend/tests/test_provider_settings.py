import pytest
import json
from unittest.mock import patch

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.app_settings import AppSetting


@pytest.fixture(autouse=True)
def provider_encryption_key(monkeypatch, request):
    import secrets
    from app.agents.services.crypto import _fernet

    key = getattr(request, "param", secrets.token_hex(32))
    monkeypatch.setattr(get_settings(), "secret_key", SecretStr(key))
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_encryption_key", ["change-me-in-production", "dev-secret-change-in-production", "short-custom-key"], indirect=True)
async def test_default_master_key_cannot_store_provider_secrets(
    client, admin_auth_headers, session
):
    response = await client.patch(
        "/api/admin/provider-settings/pluggy", headers=admin_auth_headers,
        json={"values": {"pluggy_client_id": "test-id", "pluggy_client_secret": "test-secret"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid provider settings"
    assert await session.get(AppSetting, "pluggy_client_id") is None
    assert await session.get(AppSetting, "pluggy_client_secret") is None
    status = await client.get("/api/admin/provider-settings", headers=admin_auth_headers)
    assert all(not p["can_store_secrets"] for p in status.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_encryption_key", [""], indirect=True)
async def test_empty_master_key_cannot_store_secrets(session, clean_db):
    from app.services.provider_settings import update_provider

    with pytest.raises(ValueError, match="SECRET_KEY"):
        await update_provider(session, "pluggy", {"pluggy_client_secret": "test-secret"})
    assert await session.get(AppSetting, "pluggy_client_secret") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_encryption_key", ["change-me-in-production"], indirect=True)
async def test_default_key_allows_reset_and_simplefin_toggle(
    client, admin_auth_headers, session
):
    session.add(AppSetting(key="pluggy_client_secret", value="old-ciphertext"))
    await session.commit()
    response = await client.patch(
        "/api/admin/provider-settings/pluggy", headers=admin_auth_headers,
        json={"values": {"pluggy_client_secret": None}},
    )
    assert response.status_code == 200
    response = await client.patch(
        "/api/admin/provider-settings/simplefin", headers=admin_auth_headers,
        json={"values": {"simplefin_enabled": False}},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_credentials_are_write_only_and_encrypted(client, admin_auth_headers, session):
    secret = "provider-secret-for-test"
    response = await client.patch(
        "/api/admin/provider-settings/pluggy",
        headers=admin_auth_headers,
        json={"values": {"pluggy_client_id": "test-client", "pluggy_client_secret": secret}},
    )
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert secret not in response.text
    row = await session.scalar(select(AppSetting).where(AppSetting.key == "pluggy_client_secret"))
    assert row is not None and secret not in row.value
    response = await client.get("/api/admin/provider-settings", headers=admin_auth_headers)
    assert secret not in response.text
    assert row.value not in response.text
    response = await client.get(
        "/api/admin/settings/pluggy_client_secret", headers=admin_auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_availability_changes_without_restart(client, admin_auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "pluggy_client_id", "")
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr(""))
    url = "/api/admin/provider-settings/pluggy"

    async def available():
        response = await client.get("/api/connections/providers", headers=admin_auth_headers)
        return next(p["configured"] for p in response.json()["providers"] if p["name"] == "pluggy")

    assert not await available()
    await client.patch(
        url,
        headers=admin_auth_headers,
        json={"values": {"pluggy_client_id": "id", "pluggy_client_secret": "secret"}},
    )
    assert await available()
    await client.patch(
        url, headers=admin_auth_headers, json={"values": {"pluggy_client_secret": None}}
    )
    assert not await available()


@pytest.mark.asyncio
async def test_simplefin_false_overrides_enabled_environment(
    client, admin_auth_headers, session, monkeypatch
):
    from app.services.provider_settings import resolve_settings

    monkeypatch.setattr(get_settings(), "simplefin_enabled", True)
    response = await client.patch(
        "/api/admin/provider-settings/simplefin",
        headers=admin_auth_headers,
        json={"values": {"simplefin_enabled": False}},
    )
    assert response.json()["configured"] is False
    assert (await resolve_settings(session)).simplefin_enabled is False
    await client.patch(
        "/api/admin/provider-settings/simplefin",
        headers=admin_auth_headers,
        json={"values": {"simplefin_enabled": None}},
    )
    assert (await resolve_settings(session)).simplefin_enabled is True


@pytest.mark.asyncio
async def test_unreadable_override_is_reported_without_environment_fallback(
    session, clean_db, monkeypatch
):
    from app.services.provider_settings import resolve_settings, provider_status

    monkeypatch.setattr(get_settings(), "openexchangerates_app_id", "environment")
    session.add(AppSetting(key="openexchangerates_app_id", value="broken-ciphertext"))
    await session.commit()
    assert (await resolve_settings(session)).openexchangerates_app_id == ""
    status = next(p for p in await provider_status(session) if p["name"] == "openexchangerates")
    assert not status["configured"]
    assert status["fields"]["openexchangerates_app_id"]["invalid"]


@pytest.mark.asyncio
async def test_private_key_override_wins_over_environment_file(
    client, admin_auth_headers, session, monkeypatch
):
    from app.services.provider_settings import resolve_settings

    monkeypatch.setattr(
        get_settings(), "enable_banking_private_key_file", "/existing/environment.pem"
    )
    pem = "-----BEGIN PRIVATE KEY-----\n" + "a" * 4000 + "\n-----END PRIVATE KEY-----"
    response = await client.patch(
        "/api/admin/provider-settings/enable_banking",
        headers=admin_auth_headers,
        json={"values": {"enable_banking_private_key": pem}},
    )
    assert response.status_code == 200
    settings = await resolve_settings(session)
    assert settings.enable_banking_private_key.get_secret_value() == pem
    assert settings.enable_banking_private_key_file == ""
    response = await client.patch(
        "/api/admin/provider-settings/enable_banking",
        headers=admin_auth_headers,
        json={"values": {"enable_banking_private_key": None}},
    )
    assert response.status_code == 200
    assert (
        await resolve_settings(session)
    ).enable_banking_private_key_file == "/existing/environment.pem"


@pytest.mark.asyncio
@pytest.mark.parametrize("file_contents", [None, "", "private-key-file-contents"])
async def test_private_key_fallback_requires_a_readable_nonempty_file(
    session, clean_db, monkeypatch, tmp_path, file_contents
):
    from app.services.provider_settings import provider_status, update_provider, resolve_settings
    from app.providers import all_known_providers

    key_file = tmp_path / "environment.pem"
    if file_contents is not None:
        key_file.write_text(file_contents, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "enable_banking_app_id", "environment-app-id")
    monkeypatch.setattr(get_settings(), "enable_banking_private_key", SecretStr(""))
    monkeypatch.setattr(get_settings(), "enable_banking_private_key_file", str(key_file))
    await update_provider(session, "enable_banking", {"enable_banking_private_key": "saved-key"})
    status = next(p for p in await provider_status(session) if p["name"] == "enable_banking")
    assert status["configured"] is True
    assert status["fields"]["enable_banking_private_key"]["environment_configured"] is bool(file_contents)
    await update_provider(session, "enable_banking", {"enable_banking_private_key": None})
    status = next(p for p in await provider_status(session) if p["name"] == "enable_banking")
    assert status["configured"] is bool(file_contents)
    available = all_known_providers(await resolve_settings(session))
    assert next(p for p in available if p["name"] == "enable_banking")["configured"] is bool(file_contents)


@pytest.mark.asyncio
@pytest.mark.parametrize("file_contents", [None, ""])
async def test_private_key_file_takes_precedence_over_raw_environment_key(
    session, clean_db, monkeypatch, tmp_path, file_contents
):
    from app.providers import all_known_providers
    from app.services.provider_settings import provider_status, resolve_settings

    key_file = tmp_path / "environment.pem"
    if file_contents is not None:
        key_file.write_text(file_contents, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "enable_banking_app_id", "environment-app-id")
    monkeypatch.setattr(
        get_settings(), "enable_banking_private_key", SecretStr("raw-environment-key")
    )
    monkeypatch.setattr(get_settings(), "enable_banking_private_key_file", str(key_file))

    status = next(
        provider
        for provider in await provider_status(session)
        if provider["name"] == "enable_banking"
    )
    assert status["configured"] is False
    assert (
        status["fields"]["enable_banking_private_key"]["environment_configured"]
        is False
    )
    available = all_known_providers(await resolve_settings(session))
    assert next(
        provider for provider in available if provider["name"] == "enable_banking"
    )["configured"] is False


@pytest.mark.asyncio
async def test_unconfigured_fx_refresh_is_explicit(client, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "openexchangerates_app_id", "")
    response = await client.post("/api/fx-rates/refresh", headers=auth_headers)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_provider_settings_require_admin(client, auth_headers):
    assert (
        await client.get("/api/admin/provider-settings", headers=auth_headers)
    ).status_code == 403
    response = await client.patch(
        "/api/admin/provider-settings/pluggy",
        headers=auth_headers,
        json={"values": {"pluggy_client_secret": "unauthorized"}},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_runtime_override_reset_and_environment_fallback(
    client, admin_auth_headers, session, monkeypatch
):
    from app.services.provider_settings import resolve_settings

    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr("environment"))
    url = "/api/admin/provider-settings/pluggy"
    response = await client.patch(
        url, headers=admin_auth_headers, json={"values": {"pluggy_client_secret": "saved"}}
    )
    assert response.json()["fields"]["pluggy_client_secret"]["environment_configured"] is True
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr(""))
    status = await client.get("/api/admin/provider-settings", headers=admin_auth_headers)
    pluggy = next(p for p in status.json() if p["name"] == "pluggy")
    assert pluggy["fields"]["pluggy_client_secret"]["environment_configured"] is False
    assert pluggy["configured"] is response.json()["configured"]
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr("environment"))
    assert (await resolve_settings(session)).pluggy_client_secret.get_secret_value() == "saved"
    async with AsyncSession(bind=session.bind) as other_process_session:
        assert (
            await resolve_settings(other_process_session)
        ).pluggy_client_secret.get_secret_value() == "saved"
    await client.patch(url, headers=admin_auth_headers, json={"values": {"pluggy_client_id": "id"}})
    assert (await resolve_settings(session)).pluggy_client_secret.get_secret_value() == "saved"
    await client.patch(
        url, headers=admin_auth_headers, json={"values": {"pluggy_client_secret": None}}
    )
    assert (
        await resolve_settings(session)
    ).pluggy_client_secret.get_secret_value() == "environment"


@pytest.mark.asyncio
async def test_invalid_values_do_not_echo_secrets(client, admin_auth_headers):
    secret = "do-not-echo"
    for values in ({"unknown": secret}, {"pluggy_client_secret": {"nested": secret}}):
        response = await client.patch(
            "/api/admin/provider-settings/pluggy",
            headers=admin_auth_headers,
            json={"values": values},
        )
        assert response.status_code == 400
        assert secret not in response.text


@pytest.mark.asyncio
async def test_pluggy_reauthenticates_after_credential_change(session, clean_db):
    from app.services.provider_settings import resolve_settings, update_provider
    from app.providers import get_provider
    from app.providers.pluggy import PluggyProvider

    requests = []

    def respond(request):
        credentials = json.loads(request.content)
        requests.append(credentials)
        return httpx.Response(200, json={"apiKey": credentials["clientSecret"] + "-token"})

    client_type = httpx.AsyncClient

    def client_factory(**kwargs):
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    with patch("app.providers.pluggy.httpx.AsyncClient", side_effect=client_factory):
        for secret in ("first-secret", "second-secret"):
            await update_provider(
                session, "pluggy", {"pluggy_client_id": "test-id", "pluggy_client_secret": secret}
            )
            provider = get_provider("pluggy", settings=await resolve_settings(session))
            assert isinstance(provider, PluggyProvider)
            assert await provider._ensure_api_key() == secret + "-token"
    assert [request["clientSecret"] for request in requests] == ["first-secret", "second-secret"]


@pytest.mark.asyncio
async def test_rate_provider_uses_saved_app_id(session, clean_db, caplog):
    import logging
    from app.providers.openexchangerates import OpenExchangeRatesProvider
    from app.services.provider_settings import resolve_settings, update_provider

    await update_provider(
        session, "openexchangerates", {"openexchangerates_app_id": "saved-app-id"}
    )
    app_ids = []

    def respond(request):
        app_ids.append(request.url.params["app_id"])
        return httpx.Response(200, json={"rates": {"EUR": 0.9}})

    client_type = httpx.AsyncClient
    with (
        caplog.at_level(logging.INFO, logger="httpx"),
        patch(
            "app.providers.openexchangerates.httpx.AsyncClient",
            side_effect=lambda **kwargs: client_type(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        ),
    ):
        provider = OpenExchangeRatesProvider(await resolve_settings(session))
        await provider.fetch_latest()
    assert app_ids == ["saved-app-id"]
    assert "saved-app-id" not in caplog.text
    assert "redacted" in caplog.text


@pytest.mark.asyncio
async def test_rate_provider_errors_do_not_expose_app_id(session, clean_db):
    from app.providers.openexchangerates import OpenExchangeRatesProvider
    from app.services.provider_settings import resolve_settings, update_provider

    await update_provider(
        session, "openexchangerates", {"openexchangerates_app_id": "private-app-id"}
    )
    client_type = httpx.AsyncClient
    with patch(
        "app.providers.openexchangerates.httpx.AsyncClient",
        side_effect=lambda **kwargs: client_type(
            transport=httpx.MockTransport(lambda request: httpx.Response(401)), **kwargs
        ),
    ):
        provider = OpenExchangeRatesProvider(await resolve_settings(session))
        with pytest.raises(ValueError, match="Exchange-rate provider request failed") as error:
            await provider.fetch_latest()
    assert "private-app-id" not in str(error.value)
