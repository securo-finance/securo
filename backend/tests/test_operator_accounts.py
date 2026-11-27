"""The operator opens an account in one call on a managed instance (#1965)."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager
from app.core.config import get_settings
from app.models.account import Account
from app.models.admin_access_log import AdminAccessLog, AdminAccessLogViewer
from app.models.category import Category
from app.models.rule import Rule
from app.models.user import User
from app.models.user_quota import UserQuota
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceTaxId
from app.schemas.user import UserUpdate
from app.services import operator_account_service

TOKEN = "operator-secret-token"
URL = "/api/operator/accounts"


@pytest.fixture(autouse=True)
def _clean(clean_db):
    """Each test starts from an empty database, with or without `test_user`."""


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    return {"Authorization": f"Bearer {TOKEN}"}


def _body(**overrides) -> dict:
    body = {
        "email": "ana@example.com",
        "external_reference": "crm-0042",
        "language": "pt-BR",
        "workspaces": [{"name": "Casa", "kind": "personal", "default_currency": "BRL"}],
        "quotas": {"max_personal_workspaces": 1, "max_business_workspaces": 1, "max_bank_connections": 3},
    }
    return body | overrides


BUSINESS = {
    "name": "Ana Design",
    "kind": "business",
    "default_currency": "BRL",
    "legal_name": "Ana Design Ltda",
    "tax_jurisdiction": "BR",
    "tax_ids": [{"kind": "cnpj", "value": "11.222.333/0001-81"}],
}


async def _count(session: AsyncSession, model, *where) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def _accounts(session: AsyncSession, email: str) -> list[User]:
    return list(await session.scalars(select(User).where(func.lower(User.email) == email.lower())))


# --- Who reaches the route -------------------------------------------------------


@pytest.mark.asyncio
async def test_with_the_switch_off_the_route_does_not_exist(operator, client: AsyncClient, session: AsyncSession):
    resp = await client.post(URL, json=_body(), headers=operator)
    assert resp.status_code == 404
    assert await _accounts(session, "ana@example.com") == []


@pytest.mark.asyncio
async def test_without_the_operator_token_the_route_does_not_exist(managed, client: AsyncClient, admin_auth_headers):
    resp = await client.post(URL, json=_body(), headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_an_instance_superuser_is_refused(
    managed, operator, client: AsyncClient, session: AsyncSession, admin_auth_headers
):
    resp = await client.post(URL, json=_body(), headers=admin_auth_headers)
    assert resp.status_code == 403
    assert await _accounts(session, "ana@example.com") == []


@pytest.mark.asyncio
async def test_an_access_token_is_refused(
    managed, operator, client: AsyncClient, session: AsyncSession, auth_headers, test_workspace
):
    made = await client.post(
        "/api/access-tokens",
        json={"name": "script", "scopes": ["accounts:write"]},
        headers=auth_headers | {"X-Workspace-Id": str(test_workspace.id)},
    )
    assert made.status_code == 201, made.text
    resp = await client.post(URL, json=_body(), headers={"Authorization": f"Bearer {made.json()['token']}"})
    assert resp.status_code == 403
    assert await _accounts(session, "ana@example.com") == []


# --- Opening ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_call_opens_the_account_ready_to_use(managed, operator, client: AsyncClient, session: AsyncSession):
    resp = await client.post(URL, json=_body(), headers=operator)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["created"] is True and data["external_reference"] == "crm-0042"
    assert data["quotas"] == {
        "max_personal_workspaces": 1, "max_business_workspaces": 1,
        "max_bank_connections": 3, "max_members_per_workspace": None,
    }

    [user] = await _accounts(session, "ana@example.com")
    assert str(user.id) == data["user_id"]
    assert user.external_reference == "crm-0042"
    assert user.is_active and not user.is_superuser and not user.is_verified
    prefs = user.preferences or {}
    assert prefs.get("language") == "pt-BR" and prefs.get("currency_display") == "BRL"

    [ws] = data["workspaces"]
    workspace = await session.get(Workspace, uuid.UUID(ws["id"]))
    assert workspace is not None
    assert (workspace.name, workspace.kind, workspace.default_currency, workspace.locale) == (
        "Casa", "personal", "BRL", "pt-BR",
    )
    assert workspace.created_by_user_id == user.id
    member = await session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user.id)
    )
    assert member is not None and member.role == "owner"
    # Seeded like a workspace created from the app, in the account's language.
    [wallet] = list(await session.scalars(select(Account).where(Account.workspace_id == workspace.id)))
    assert (wallet.name, wallet.currency, wallet.user_id) == ("Carteira", "BRL", user.id)
    assert await _count(session, Category, Category.workspace_id == workspace.id) > 0
    assert await _count(session, Rule, Rule.workspace_id == workspace.id) > 0


@pytest.mark.asyncio
async def test_the_account_cannot_be_signed_in_to(managed, operator, client: AsyncClient):
    assert (await client.post(URL, json=_body(), headers=operator)).status_code == 201
    for password in ("", "x", "crm-0042", "ana@example.com"):
        login = await client.post("/api/auth/login", data={"username": "ana@example.com", "password": password})
        assert login.status_code in (400, 422), (password, login.text)
        assert "access_token" not in login.text


@pytest.mark.asyncio
async def test_the_account_has_no_password_set_until_the_person_sets_one(
    managed, operator, client: AsyncClient, session: AsyncSession
):
    """The password column always holds a hash, so it cannot say this (#1966)."""
    assert (await client.post(URL, json=_body(), headers=operator)).status_code == 201
    [user] = await _accounts(session, "ana@example.com")
    assert user.hashed_password and user.password_set_at is None

    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    await manager.update(UserUpdate(password="her-own-pass-12"), user)
    session.expire_all()
    [user] = await _accounts(session, "ana@example.com")
    assert user.password_set_at is not None
    login = await client.post("/api/auth/login", data={"username": "ana@example.com", "password": "her-own-pass-12"})
    assert login.status_code == 200, login.text


@pytest.mark.asyncio
async def test_the_same_call_again_changes_nothing(managed, operator, client: AsyncClient, session: AsyncSession):
    first = await client.post(URL, json=_body(workspaces=[_body()["workspaces"][0], BUSINESS]), headers=operator)
    assert first.status_code == 201, first.text
    again = await client.post(
        URL,
        # A retry answers what exists; it never applies a changed body.
        json=_body(workspaces=[BUSINESS], quotas={"max_bank_connections": 99}, email="ANA@example.com"),
        headers=operator,
    )
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["user_id"] == first.json()["user_id"]
    assert again.json()["workspaces"] == first.json()["workspaces"]
    assert again.json()["quotas"] == first.json()["quotas"]

    user_id = uuid.UUID(first.json()["user_id"])
    assert len(await _accounts(session, "ana@example.com")) == 1
    assert await _count(session, Workspace, Workspace.created_by_user_id == user_id) == 2
    assert await _count(session, AdminAccessLog, AdminAccessLog.target_user_id == user_id) == 1


@pytest.mark.asyncio
async def test_a_reference_already_used_for_another_email_is_refused(managed, operator, client: AsyncClient, session):
    assert (await client.post(URL, json=_body(), headers=operator)).status_code == 201
    resp = await client.post(URL, json=_body(email="bia@example.com"), headers=operator)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "external_reference_taken"
    assert await _accounts(session, "bia@example.com") == []


def _lose_the_race():
    """The first lookup misses, as if the winner had not committed yet."""
    real = operator_account_service._existing
    calls = 0

    async def existing(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(*args, **kwargs)

    return patch.object(operator_account_service, "_existing", existing)


@pytest.mark.asyncio
async def test_losing_the_race_answers_what_the_winner_opened(
    managed, operator, client: AsyncClient, session: AsyncSession
):
    first = await client.post(URL, json=_body(), headers=operator)
    assert first.status_code == 201, first.text
    with _lose_the_race():
        again = await client.post(URL, json=_body(), headers=operator)
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["user_id"] == first.json()["user_id"]
    assert again.json()["workspaces"] == first.json()["workspaces"]

    user_id = uuid.UUID(first.json()["user_id"])
    assert len(await _accounts(session, "ana@example.com")) == 1
    assert await _count(session, Workspace, Workspace.created_by_user_id == user_id) == 1
    assert await _count(session, AdminAccessLog, AdminAccessLog.target_user_id == user_id) == 1


@pytest.mark.asyncio
async def test_losing_the_race_to_another_email_is_refused(
    managed, operator, client: AsyncClient, session: AsyncSession
):
    assert (await client.post(URL, json=_body(), headers=operator)).status_code == 201
    with _lose_the_race():
        resp = await client.post(URL, json=_body(email="bia@example.com"), headers=operator)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "external_reference_taken"
    assert await _accounts(session, "bia@example.com") == []


@pytest.mark.asyncio
async def test_a_taken_email_is_refused_not_merged(
    managed, operator, client: AsyncClient, session: AsyncSession, test_user
):
    user_id, email = test_user.id, test_user.email
    before = await _count(session, Workspace, Workspace.created_by_user_id == user_id)
    resp = await client.post(URL, json=_body(email=email.upper()), headers=operator)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "email_taken"
    session.expire_all()
    user = await session.get(User, user_id)
    assert user is not None and user.external_reference is None
    assert await _count(session, Workspace, Workspace.created_by_user_id == user_id) == before
    assert await _count(session, UserQuota, UserQuota.user_id == user_id) == 0


@pytest.mark.asyncio
async def test_a_business_workspace_keeps_its_legal_fields(managed, operator, client: AsyncClient, session: AsyncSession):
    resp = await client.post(URL, json=_body(language="en", workspaces=[BUSINESS]), headers=operator)
    assert resp.status_code == 201, resp.text
    workspace = await session.get(Workspace, uuid.UUID(resp.json()["workspaces"][0]["id"]))
    assert workspace is not None
    assert (workspace.kind, workspace.legal_name, workspace.tax_jurisdiction) == ("business", "Ana Design Ltda", "BR")
    [tax_id] = list(await session.scalars(select(WorkspaceTaxId).where(WorkspaceTaxId.workspace_id == workspace.id)))
    assert (tax_id.kind, tax_id.value) == ("cnpj", "11222333000181")
    [wallet] = list(await session.scalars(select(Account).where(Account.workspace_id == workspace.id)))
    assert wallet.name == "Wallet"


@pytest.mark.asyncio
async def test_legal_fields_on_a_personal_workspace_are_refused(managed, operator, client: AsyncClient, session):
    personal = {"name": "Casa", "kind": "personal", "default_currency": "BRL", "legal_name": "Ana"}
    resp = await client.post(URL, json=_body(workspaces=[personal]), headers=operator)
    assert resp.status_code == 422
    assert await _accounts(session, "ana@example.com") == []


@pytest.mark.asyncio
async def test_an_invalid_tax_id_opens_nothing(managed, operator, client: AsyncClient, session: AsyncSession):
    bad = BUSINESS | {"tax_ids": [{"kind": "cnpj", "value": "11.222.333/0001-00"}]}
    resp = await client.post(URL, json=_body(workspaces=[_body()["workspaces"][0], bad]), headers=operator)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"].startswith("invalid_tax_id")
    assert await _accounts(session, "ana@example.com") == []
    assert await _count(session, Workspace, Workspace.name == "Casa") == 0


# --- One transaction ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_limits_are_written_in_the_same_transaction(managed, operator, client: AsyncClient, session):
    resp = await client.post(URL, json=_body(), headers=operator)
    assert resp.status_code == 201
    quota = await session.get(UserQuota, uuid.UUID(resp.json()["user_id"]))
    assert quota is not None
    assert (quota.max_personal_workspaces, quota.max_bank_connections) == (1, 3)

    # A failure after the limits are written takes the account, its limits and
    # its workspaces back with it.
    with patch.object(operator_account_service, "_record", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            await client.post(URL, json=_body(email="bia@example.com", external_reference="crm-0043"), headers=operator)
    assert await _accounts(session, "bia@example.com") == []
    assert await _count(session, UserQuota) == 1
    assert await _count(session, Workspace, Workspace.name == "Casa") == 1


@pytest.mark.asyncio
async def test_workspaces_over_the_limits_open_nothing(managed, operator, client: AsyncClient, session: AsyncSession):
    two = [_body()["workspaces"][0], _body()["workspaces"][0] | {"name": "Outra"}]
    resp = await client.post(URL, json=_body(workspaces=two), headers=operator)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "quota_exceeded"
    assert await _accounts(session, "ana@example.com") == []
    assert await _count(session, UserQuota) == 0


# --- The record ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_opening_is_recorded_with_the_operator_as_actor(managed, operator, client: AsyncClient, session):
    resp = await client.post(URL, json=_body(), headers=operator)
    user_id = uuid.UUID(resp.json()["user_id"])
    [entry] = list(await session.scalars(select(AdminAccessLog).where(AdminAccessLog.target_user_id == user_id)))
    assert entry.actor_id is None and entry.actor_email == "operator"
    assert entry.action == "account.open" and entry.target_email == "ana@example.com"
    assert entry.reason
    assert entry.changes["external_reference"] == {"from": None, "to": "crm-0042"}
    assert entry.changes["workspaces"]["to"] == [{"id": resp.json()["workspaces"][0]["id"], "kind": "personal"}]
    assert entry.changes["quotas"]["to"]["max_bank_connections"] == 3
    viewers = set(await session.scalars(select(AdminAccessLogViewer.viewer_id).where(AdminAccessLogViewer.entry_id == entry.id)))
    assert viewers == {user_id}
