"""Default limits for accounts with no row of their own (ADR 0049, #1971).

The defaults are read when a limit is checked, so every path that creates a
user follows them without writing anything, an account with its own row is
never touched by them, and changing them rewrites no account.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import oidc_auth
from app.core.config import get_settings
from app.models.app_settings import AppSetting
from app.models.user import User
from app.models.user_quota import UserQuota
from app.services import quota_service
from tests import test_oidc_auth

FakeRedis = test_oidc_auth.FakeRedis
oidc_settings = test_oidc_auth.oidc_settings

TOKEN = "operator-secret-token"
DEFAULTS_URL = "/api/operator/quota-defaults"
ONE_PERSONAL = {"max_personal_workspaces": 1}


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    return {"Authorization": f"Bearer {TOKEN}"}


async def _second_personal(client: AsyncClient, token: str):
    return await client.post(
        "/api/workspaces", json={"name": "Second"}, headers={"Authorization": f"Bearer {token}"},
    )


async def _sign_up(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"email": email, "password": "newpass123"})
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "newpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _refused_on(resp, counter: str) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "quota_exceeded"
    assert resp.json()["detail"]["counter"] == counter


@pytest.mark.asyncio
async def test_no_default_is_unbounded(managed, session: AsyncSession, test_user):
    assert await quota_service.effective_limits(session, test_user.id) == (
        dict.fromkeys(quota_service.COUNTERS), quota_service.UNBOUNDED,
    )
    for _ in range(3):
        await quota_service.check_new_workspace(session, test_user.id, "personal")


@pytest.mark.asyncio
async def test_writing_the_defaults_twice_from_none_updates_one_setting(session: AsyncSession):
    assert await quota_service.get_defaults(session) is None
    await quota_service.set_defaults(session, ONE_PERSONAL)
    await quota_service.set_defaults(session, {"max_bank_connections": 3})
    await session.commit()

    assert await quota_service.get_defaults(session) == {
        **dict.fromkeys(quota_service.COUNTERS), "max_bank_connections": 3,
    }
    assert await session.scalar(
        select(func.count()).select_from(AppSetting).where(AppSetting.key == quota_service.DEFAULTS_KEY)
    ) == 1


@pytest.mark.asyncio
async def test_a_default_holds_a_fresh_sign_up(managed, operator, client: AsyncClient):
    resp = await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=operator)
    assert resp.status_code == 200, resp.text

    token = await _sign_up(client, "fresh@example.com")
    _refused_on(await _second_personal(client, token), "max_personal_workspaces")


@pytest.mark.asyncio
async def test_a_default_holds_an_oidc_first_login(
    managed, operator, client: AsyncClient, session: AsyncSession, clean_db, oidc_settings, monkeypatch,
):
    await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=operator)
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "user-sub", "email": "oidc@example.com", "email_verified": True, "name": "OIDC User"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    resp = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert resp.status_code == 307
    token = parse_qs(urlparse(resp.headers["location"]).fragment)["access_token"][0]

    _refused_on(await _second_personal(client, token), "max_personal_workspaces")
    assert await session.scalar(select(func.count()).select_from(UserQuota)) == 0


@pytest.mark.asyncio
async def test_an_account_with_its_own_row_ignores_the_default(
    managed, operator, client: AsyncClient, session: AsyncSession, test_user, auth_headers,
):
    await client.put(DEFAULTS_URL, json={"max_personal_workspaces": 0}, headers=operator)
    # A row of its own with all four null is unbounded, not "use the default".
    resp = await client.put(f"/api/operator/quotas/{test_user.id}", json={}, headers=operator)
    assert resp.json()["source"] == "own"

    resp = await client.post("/api/workspaces", json={"name": "Second"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    resp = await client.get(f"/api/operator/quotas/{test_user.id}", headers=operator)
    assert resp.json()["source"] == "own" and resp.json()["max_personal_workspaces"] is None


@pytest.mark.asyncio
async def test_changing_the_default_moves_accounts_without_a_row_and_rewrites_none(
    managed, operator, client: AsyncClient, session: AsyncSession, test_user, auth_headers,
):
    await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=operator)
    _refused_on(await client.post("/api/workspaces", json={"name": "Second"}, headers=auth_headers),
                "max_personal_workspaces")

    await client.put(DEFAULTS_URL, json={"max_personal_workspaces": 2}, headers=operator)
    resp = await client.post("/api/workspaces", json={"name": "Second"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text

    assert (await client.delete(DEFAULTS_URL, headers=operator)).status_code == 204
    resp = await client.post("/api/workspaces", json={"name": "Third"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    assert await session.scalar(select(func.count()).select_from(UserQuota)) == 0


@pytest.mark.asyncio
async def test_the_operator_reads_say_where_the_limits_come_from(
    managed, operator, client: AsyncClient, test_user,
):
    url = f"/api/operator/quotas/{test_user.id}"
    assert (await client.get(DEFAULTS_URL, headers=operator)).json() == {
        "is_set": False, **dict.fromkeys(quota_service.COUNTERS),
    }
    assert (await client.get(url, headers=operator)).json()["source"] == "none"

    body = {"max_personal_workspaces": 1, "max_business_workspaces": 0, "max_bank_connections": 2, "max_members_per_workspace": 3}
    assert (await client.put(DEFAULTS_URL, json=body, headers=operator)).json() == {"is_set": True, **body}
    assert (await client.get(DEFAULTS_URL, headers=operator)).json() == {"is_set": True, **body}

    read = (await client.get(url, headers=operator)).json()
    assert read == {"user_id": str(test_user.id), "source": "default", **body}
    fit = await client.post(f"{url}/fit-check", json=body, headers=operator)
    assert fit.json()["source"] == "default" and fit.json()["allowed"] is True

    await client.put(url, json=body, headers=operator)
    assert (await client.get(url, headers=operator)).json()["source"] == "own"
    assert (await client.post(f"{url}/fit-check", json=body, headers=operator)).json()["source"] == "own"


@pytest.mark.asyncio
async def test_the_default_is_ignored_with_the_switch_off(
    operator, client: AsyncClient, session: AsyncSession, test_user,
):
    await client.put(DEFAULTS_URL, json={"max_personal_workspaces": 0}, headers=operator)
    await quota_service.check_new_workspace(session, test_user.id, "personal")


@pytest.mark.asyncio
async def test_only_the_operator_writes_the_default(
    client: AsyncClient, monkeypatch, admin_auth_headers,
):
    assert (await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=admin_auth_headers)).status_code == 404
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    assert (await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=admin_auth_headers)).status_code == 403
    assert (await client.delete(DEFAULTS_URL, headers=admin_auth_headers)).status_code == 403
    # Nor through the admin screen, whose keys are an allowlist.
    resp = await client.patch(
        f"/api/admin/settings/{quota_service.DEFAULTS_KEY}", json={"value": "{}"}, headers=admin_auth_headers,
    )
    assert resp.status_code == 400
    resp = await client.delete(f"/api/admin/settings/{quota_service.DEFAULTS_KEY}", headers=admin_auth_headers)
    assert resp.status_code == 400
    resp = await client.put(DEFAULTS_URL, json={"max_bank_connections": -1}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 422
    # Reading them is not refused: the admin sees the numbers, it cannot change them.
    operator = {"Authorization": f"Bearer {TOKEN}"}
    assert (await client.put(DEFAULTS_URL, json=ONE_PERSONAL, headers=operator)).status_code == 200
    resp = await client.get(f"/api/admin/settings/{quota_service.DEFAULTS_KEY}", headers=admin_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_a_default_below_the_count_refuses_and_removes_nothing(managed, session: AsyncSession, test_user):
    """The defaults change the maximum, not the count: an existing excess is reported, not removed."""
    await quota_service.set_defaults(session, {"max_personal_workspaces": 0})
    with pytest.raises(HTTPException) as refused:
        await quota_service.check_new_workspace(session, test_user.id, "personal")
    detail = refused.value.detail
    assert isinstance(detail, dict) and detail["current"] == 1 and detail["maximum"] == 0
    fit = await quota_service.check_fit(session, test_user.id, {"max_personal_workspaces": 0})
    assert fit.source == quota_service.DEFAULT and not fit.allowed
    assert await session.get(User, test_user.id) is not None
