"""Every path that builds a provider for a workspace uses that workspace's credentials.

The provider is recorded as `connection_service` builds it, so a path that
resolves the instance settings instead shows up as the instance client.
"""

import secrets
from unittest.mock import patch

import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.agents.services.crypto import _fernet
from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.providers import get_provider as real_get_provider
from app.providers.pluggy import PluggyProvider
from app.services import connection_service
from app.services.provider_settings import set_workspace_credentials

OWN = {"pluggy_client_id": "own-client", "pluggy_client_secret": "own-secret"}


class Reached(Exception):
    """Stops a provider before it calls Pluggy."""


@pytest.fixture(autouse=True)
def instance_client(monkeypatch):
    """A key that can store secrets, set before anything is encrypted or signed."""
    monkeypatch.setattr(get_settings(), "secret_key", SecretStr(secrets.token_hex(32)))
    monkeypatch.setattr(get_settings(), "pluggy_client_id", "instance-client")
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr("instance-secret"))
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


@pytest.fixture
def built():
    """Client id of every Pluggy provider `connection_service` builds."""
    clients: list[str] = []

    def recording(name, settings=None):
        provider = real_get_provider(name, settings=settings)
        if isinstance(provider, PluggyProvider):
            clients.append(provider.settings.pluggy_client_id)
        return provider

    async def no_network(self):
        raise Reached

    with (
        patch.object(connection_service, "get_provider", recording),
        patch.object(PluggyProvider, "_ensure_api_key", no_network),
    ):
        yield clients


@pytest_asyncio.fixture
async def own_client(session, test_workspace, test_user):
    """The test workspace with its own Pluggy client and one Pluggy connection."""
    await set_workspace_credentials(session, "pluggy", test_workspace.id, dict(OWN))
    connection = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-1",
        institution_name="Banco",
        credentials={"item_id": "item-1"},
    )
    session.add(connection)
    await session.commit()
    return connection


@pytest.mark.asyncio
async def test_routes_use_the_workspace_client(client, auth_headers, own_client, built):
    await client.post("/api/connections/connect-token", headers=auth_headers, json={"provider": "pluggy"})
    await client.post(f"/api/connections/{own_client.id}/reconnect-token", headers=auth_headers)
    await client.get("/api/connections/pluggy/institutions", headers=auth_headers)
    # Pluggy has no redirect flow; the provider is built for the workspace before that fails.
    with pytest.raises(Exception):
        await client.post("/api/connections/oauth/url", headers=auth_headers, json={"provider": "pluggy"})
    await client.post(
        "/api/connections/oauth/callback", headers=auth_headers, json={"code": "item-2", "provider": "pluggy"}
    )
    await client.post(f"/api/connections/{own_client.id}/sync", headers=auth_headers)
    # connect token, reconnect token, institutions, OAuth URL, callback, sync
    assert len(built) >= 6
    assert set(built) == {"own-client"}


@pytest.mark.asyncio
async def test_sync_reauth_holdings_and_trades_use_the_workspace_client(
    session, test_user, test_workspace, own_client, built
):
    # Rollbacks and commits below expire the fixtures' objects.
    user_id, workspace_id, connection_id = test_user.id, test_workspace.id, own_client.id
    with pytest.raises(Exception):
        await connection_service.sync_connection(session, connection_id, workspace_id, user_id)
    with pytest.raises(Exception):
        await connection_service.get_reauth_url(session, connection_id, workspace_id, user_id)
    # The failed sync rolled back and expired the connection.
    await session.rollback()
    await session.refresh(own_client)
    await connection_service._fetch_holdings(session, own_client, own_client.credentials)
    own_client.settings = {connection_service._HOLDING_TRADES: {}}
    await session.commit()
    await session.refresh(own_client)
    await connection_service._sync_holdings(
        session, user_id, own_client, own_client.credentials, holdings=[]
    )
    assert len(built) >= 4
    assert set(built) == {"own-client"}


@pytest.mark.asyncio
async def test_picker_shows_pluggy_when_only_the_workspace_has_a_client(
    client, auth_headers, session, test_workspace, built, monkeypatch
):
    monkeypatch.setattr(get_settings(), "pluggy_client_id", "")
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr(""))

    def pluggy_configured(response):
        assert response.status_code == 200
        return next(p for p in response.json()["providers"] if p["name"] == "pluggy")["configured"]

    assert pluggy_configured(await client.get("/api/connections/providers", headers=auth_headers)) is False
    await set_workspace_credentials(session, "pluggy", test_workspace.id, dict(OWN))
    assert pluggy_configured(await client.get("/api/connections/providers", headers=auth_headers)) is True


@pytest.mark.asyncio
async def test_picker_hides_pluggy_when_the_workspace_client_cannot_be_read(
    client, auth_headers, session, test_workspace, built
):
    from sqlalchemy import update

    from app.models.workspace_provider_credential import WorkspaceProviderCredential

    await set_workspace_credentials(session, "pluggy", test_workspace.id, dict(OWN))
    await session.execute(update(WorkspaceProviderCredential).values(value="not-a-token"))
    await session.commit()

    response = await client.get("/api/connections/providers", headers=auth_headers)
    assert response.status_code == 200
    # The instance has a client, but this workspace must not fall back to it.
    assert next(p for p in response.json()["providers"] if p["name"] == "pluggy")["configured"] is False
