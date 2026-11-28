"""Which modules an account's workspaces may use on a managed instance (ADR 0054).

A neutral list per account, written by the operator, copied onto the
workspaces the account created and read by the module resolver only with
MANAGED_INSTANCE on.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import User
from app.models.user_module_limit import UserModuleLimit
from app.models.workspace import Workspace
from app.services import module_limit_service, workspace_service
from app.services.module_service import ModuleId, resolve_modules

TOKEN = "operator-secret-token"
EVERY_PERSONAL = sorted(m.value for m in ModuleId if m is not ModuleId.INVOICES)


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    return {"Authorization": f"Bearer {TOKEN}"}


async def _user(session: AsyncSession, email: str) -> User:
    user = User(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    return user


def _without(*modules: ModuleId) -> list[str]:
    return [m.value for m in ModuleId if m not in modules]


async def test_no_list_is_every_module(managed, session: AsyncSession, test_user, test_workspace):
    assert await module_limit_service.get_limit(session, test_user.id) is None
    assert test_workspace.module_allowlist is None
    assert resolve_modules(test_workspace) == EVERY_PERSONAL


async def test_off_the_list_is_never_read(session: AsyncSession, test_user, test_workspace):
    await module_limit_service.set_limit(session, test_user.id, ["transactions"])
    assert test_workspace.module_allowlist == ["transactions"]
    assert resolve_modules(test_workspace) == EVERY_PERSONAL


async def test_a_module_off_the_list_is_off_in_an_owned_workspace(
    managed, session: AsyncSession, test_user, test_workspace
):
    await module_limit_service.set_limit(session, test_user.id, _without(ModuleId.SPLIT_GROUPS))
    modules = resolve_modules(test_workspace)
    assert "split_groups" not in modules
    assert "transactions" in modules


async def test_the_list_never_grants_what_the_kind_does_not(
    managed, session: AsyncSession, test_user, test_workspace
):
    await module_limit_service.set_limit(session, test_user.id, ["invoices", "transactions"])
    assert resolve_modules(test_workspace) == ["transactions"]


async def test_a_workspace_another_account_invited_you_into_keeps_its_modules(
    managed, session: AsyncSession, test_user
):
    other = await _user(session, "other-owner@example.com")
    theirs = await workspace_service.create_workspace(session, name="Theirs", creator=other)
    await workspace_service.add_member(session, theirs.id, test_user.id)

    await module_limit_service.set_limit(session, test_user.id, ["transactions"])

    await session.refresh(theirs)
    assert theirs.module_allowlist is None
    assert resolve_modules(theirs) == EVERY_PERSONAL


async def test_a_workspace_created_later_takes_the_list(managed, session: AsyncSession, test_user):
    await module_limit_service.set_limit(session, test_user.id, ["accounts", "transactions"])
    created = await workspace_service.create_workspace(session, name="Later", creator=test_user)
    assert created.module_allowlist == ["accounts", "transactions"]
    assert resolve_modules(created) == ["accounts", "transactions"]


async def test_clearing_the_list_brings_every_module_back(
    managed, session: AsyncSession, test_user, test_workspace
):
    await module_limit_service.set_limit(session, test_user.id, ["transactions"])
    await module_limit_service.set_limit(session, test_user.id, None)
    assert await session.get(UserModuleLimit, test_user.id) is None
    assert test_workspace.module_allowlist is None
    assert resolve_modules(test_workspace) == EVERY_PERSONAL


async def test_an_unknown_module_is_refused(session: AsyncSession, test_user):
    with pytest.raises(ValueError):
        await module_limit_service.set_limit(session, test_user.id, ["not-a-module"])


async def test_the_gate_answers_404_and_the_data_survives_off_and_on(
    managed, client: AsyncClient, session: AsyncSession, test_user, auth_headers
):
    business = await workspace_service.create_workspace(
        session, name="Shop", creator=test_user, kind="business"
    )
    await session.commit()
    headers = {**auth_headers, "X-Workspace-Id": str(business.id)}

    created = await client.post(
        "/api/invoices",
        headers=headers,
        json={
            "total": "100.00",
            "due_date": "2030-01-31",
            "lines": [{"description": "Service", "quantity": "1", "unit_price": "100.00"}],
            "as_draft": True,
        },
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]

    await module_limit_service.set_limit(session, test_user.id, _without(ModuleId.INVOICES))
    await session.commit()
    assert (await client.get("/api/invoices", headers=headers)).status_code == 404
    listed = (await client.get("/api/workspaces", headers=auth_headers)).json()
    shop = next(w for w in listed if w["id"] == str(business.id))
    assert "invoices" not in shop["enabled_modules"]

    await module_limit_service.set_limit(session, test_user.id, None)
    await session.commit()
    restored = await client.get(f"/api/invoices/{invoice_id}", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["total"] in ("100.00", "100.0", 100.0)


async def test_without_operator_token_the_route_does_not_exist(
    client: AsyncClient, test_user, admin_auth_headers
):
    path = f"/api/operator/modules/{test_user.id}"
    assert (await client.get(path, headers=admin_auth_headers)).status_code == 404
    put = await client.put(path, json={"modules": []}, headers=admin_auth_headers)
    assert put.status_code == 404


async def test_an_instance_superuser_is_refused(
    operator, client: AsyncClient, test_user, admin_auth_headers
):
    path = f"/api/operator/modules/{test_user.id}"
    assert (await client.get(path, headers=admin_auth_headers)).status_code == 403


async def test_the_operator_writes_and_reads_the_list(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace
):
    path = f"/api/operator/modules/{test_user.id}"
    assert (await client.get(path, headers=operator)).json() == {
        "user_id": str(test_user.id), "modules": None,
    }

    written = await client.put(
        path, json={"modules": ["transactions", "accounts", "accounts"]}, headers=operator
    )
    assert written.status_code == 200
    assert written.json()["modules"] == ["accounts", "transactions"]
    assert (await client.get(path, headers=operator)).json()["modules"] == ["accounts", "transactions"]

    stored = await session.execute(
        select(Workspace.module_allowlist).where(Workspace.id == test_workspace.id)
    )
    assert stored.scalar_one() == ["accounts", "transactions"]

    cleared = await client.put(path, json={"modules": None}, headers=operator)
    assert cleared.json()["modules"] is None


async def test_the_operator_route_refuses_an_unknown_module_or_user(operator, client: AsyncClient, test_user):
    bad = await client.put(
        f"/api/operator/modules/{test_user.id}", json={"modules": ["nope"]}, headers=operator
    )
    assert bad.status_code == 422
    missing = await client.get(f"/api/operator/modules/{uuid.uuid4()}", headers=operator)
    assert missing.status_code == 404
