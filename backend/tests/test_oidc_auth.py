import json
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api import oidc_auth
from app.core.config import get_settings
from app.models.workspace import WorkspaceMember


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def oidc_settings(monkeypatch):
    settings = get_settings()
    old = settings.model_dump()
    settings.oidc_enabled = True
    settings.oidc_provider_name = "Pocket ID"
    settings.oidc_discovery_url = "https://id.example.com/.well-known/openid-configuration"
    settings.oidc_client_id = "securo"
    settings.oidc_client_secret = "secret"
    settings.frontend_url = "http://test"
    settings.oidc_sync_roles = False
    settings.oidc_roles_claim = "groups"
    settings.oidc_admin_roles = ""
    settings.oidc_workspace_role_map = ""
    yield settings
    for key, value in old.items():
        setattr(settings, key, value)


@pytest.mark.asyncio
async def test_oidc_config_disabled_by_default(client: AsyncClient, clean_db):
    response = await client.get("/api/auth/oidc/config")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_oidc_login_redirects_to_provider(client: AsyncClient, clean_db, oidc_settings, monkeypatch):
    fake_redis = FakeRedis()

    async def fake_discover():
        return {"authorization_endpoint": "https://id.example.com/authorize"}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)

    response = await client.get("/api/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "id.example.com"
    assert parsed.path == "/authorize"
    assert params["client_id"] == ["securo"]
    assert params["scope"] == ["openid email profile"]
    assert params["redirect_uri"] == ["http://test/api/auth/oidc/callback"]
    assert f"oidc_state:{params['state'][0]}" in fake_redis.store


@pytest.mark.asyncio
async def test_oidc_callback_creates_user_and_redirects_with_securo_token(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code):
        assert code == "abc"
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce):
        assert id_token == "id-token"
        assert nonce == "nonce123"
        return {"sub": "user-sub", "email": "oidc@example.com", "email_verified": True, "name": "OIDC User"}

    async def fake_userinfo(discovery, access_token):
        assert access_token == "provider-token"
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://test/auth/oidc/callback#access_token=")

    token = parse_qs(urlparse(location).fragment)["access_token"][0]
    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "oidc@example.com"


@pytest.mark.asyncio
async def test_oidc_callback_syncs_existing_user_admin_and_workspace_role(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_sync_roles = True
    oidc_settings.oidc_admin_roles = "securo-admins"
    oidc_settings.oidc_workspace_role_map = json.dumps(
        {
            "securo-viewers": "viewer",
            "securo-editors": "editor",
            "securo-owners": "owner",
        }
    )
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce):
        return {
            "sub": "user-sub",
            "email": "test@example.com",
            "email_verified": True,
            "groups": ["securo-admins", "securo-editors"],
        }

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    token = parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_superuser"] is True

    workspace = await client.get("/api/workspaces/current", headers={"Authorization": f"Bearer {token}"})
    assert workspace.status_code == 200
    assert workspace.json()["role"] == "editor"

    member = (
        await session.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == test_user.id))
    ).scalars().first()
    assert member.role == "editor"


@pytest.mark.asyncio
async def test_oidc_callback_sync_roles_can_revoke_admin(
    client: AsyncClient, session, test_superuser, oidc_settings, monkeypatch
):
    oidc_settings.oidc_sync_roles = True
    oidc_settings.oidc_admin_roles = "securo-admins"
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce):
        return {
            "sub": "user-sub",
            "email": "admin@example.com",
            "email_verified": True,
            "groups": ["securo-users"],
        }

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    token = parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_superuser"] is False
