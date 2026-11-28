"""The operator looks an account up on a managed instance (ADR 0049, #1966).

The answer names the account, the workspaces it created and its limits next
to the current counts, and carries nothing from inside a workspace.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import access_token_service, quota_service
from tests.conftest import engine

TOKEN = "operator-secret-token"
URL = "/api/operator/accounts"


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    return {"Authorization": f"Bearer {TOKEN}"}


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    return user


async def _workspace(session: AsyncSession, creator: User, kind: str = "personal", **extra) -> Workspace:
    workspace = Workspace(name=f"{kind} {uuid.uuid4().hex[:6]}", kind=kind, created_by_user_id=creator.id, **extra)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=creator.id, role="owner"))
    await session.flush()
    return workspace


async def _held(session: AsyncSession, test_user, test_workspace) -> tuple[Workspace, Workspace]:
    """Two more workspaces, a connection, a member, an account and a limits row."""
    test_user.password_set_at = datetime.now(timezone.utc)
    business = await _workspace(session, test_user, "business")
    archived = await _workspace(session, test_user, is_archived=True)
    session.add(BankConnection(
        workspace_id=test_workspace.id, user_id=test_user.id, provider="test",
        external_id=uuid.uuid4().hex, institution_name="Some Bank", credentials={},
    ))
    member = await _user(session, "member@example.com")
    session.add(WorkspaceMember(workspace_id=test_workspace.id, user_id=member.id, role="viewer"))
    session.add(Account(
        workspace_id=test_workspace.id, user_id=test_user.id, name="Checking Secret Name",
        type="checking", balance=12345, currency="BRL",
    ))
    await quota_service.set_quota(session, test_user.id, {
        "max_personal_workspaces": 3, "max_bank_connections": 2, "max_members_per_workspace": 5,
    })
    await session.commit()
    return business, archived


@pytest.mark.asyncio
async def test_the_route_does_not_exist_with_the_switch_off(monkeypatch, client: AsyncClient, test_user):
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    resp = await client.get(URL, params={"user_id": str(test_user.id)}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_without_a_token_configured_the_route_does_not_exist(monkeypatch, client: AsyncClient, test_user):
    monkeypatch.setattr(get_settings(), "managed_instance", True)
    resp = await client.get(URL, params={"user_id": str(test_user.id)}, headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_superuser_and_an_access_token_are_refused(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace, admin_auth_headers,
):
    params = {"user_id": str(test_user.id)}
    assert (await client.get(URL, params=params, headers=admin_auth_headers)).status_code == 403
    _, raw = await access_token_service.create_token(
        session, user_id=test_user.id, workspace_id=test_workspace.id,
        can_write=True, name="everything", scopes=list(access_token_service.ALL_SCOPES), expires_in_days=None,
    )
    assert (await client.get(URL, params=params, headers={"Authorization": f"Bearer {raw}"})).status_code == 403


@pytest.mark.asyncio
async def test_by_id_answers_the_account_its_workspaces_and_its_numbers(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace,
):
    business, archived = await _held(session, test_user, test_workspace)

    resp = await client.get(URL, params={"user_id": str(test_user.id)}, headers=operator)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(test_user.id)
    assert body["email"] == test_user.email
    assert body["has_password"] is True and body["is_active"] is True

    by_id = {w["id"]: w for w in body["workspaces"]}
    assert set(by_id) == {str(test_workspace.id), str(business.id), str(archived.id)}
    assert by_id[str(test_workspace.id)] == {
        "id": str(test_workspace.id), "name": test_workspace.name, "kind": test_workspace.kind,
        "is_archived": False, "members": 1,
    }
    assert by_id[str(business.id)]["kind"] == "business"
    assert by_id[str(archived.id)]["is_archived"] is True

    usage = {u["counter"]: (u["current"], u["maximum"]) for u in body["usage"]}
    assert usage == {
        "max_personal_workspaces": (1, 3),
        "max_business_workspaces": (1, None),
        "max_bank_connections": (1, 2),
        "max_members_per_workspace": (1, 5),
    }


@pytest.mark.asyncio
async def test_the_counts_are_the_ones_the_fit_check_reports(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace,
):
    await _held(session, test_user, test_workspace)
    quota = await quota_service.get_quota(session, test_user.id)
    limits = {c: getattr(quota, c) for c in quota_service.COUNTERS}
    fit = await quota_service.check_fit(session, test_user.id, limits)

    body = (await client.get(URL, params={"user_id": str(test_user.id)}, headers=operator)).json()
    assert [(u["counter"], u["current"], u["maximum"]) for u in body["usage"]] == [
        (f.counter, f.current, f.target) for f in fit.counters
    ]


@pytest.mark.asyncio
async def test_the_members_of_every_workspace_come_from_one_query(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace,
):
    business, archived = await _held(session, test_user, test_workspace)
    for email in ("one@example.com", "two@example.com"):
        session.add(WorkspaceMember(workspace_id=business.id, user_id=(await _user(session, email)).id, role="editor"))
    await session.commit()

    member_counts: list[str] = []

    def record(conn, cursor, statement, *args):
        if "workspace_members" in statement and "count(" in statement.lower():
            member_counts.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        body = (await client.get(URL, params={"user_id": str(test_user.id)}, headers=operator)).json()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    members = {w["id"]: w["members"] for w in body["workspaces"]}
    assert members == {str(test_workspace.id): 1, str(business.id): 2, str(archived.id): 0}
    # One grouped query for the workspaces' seats, plus the fit check's own: not one per workspace.
    assert len(member_counts) == 2, member_counts


@pytest.mark.asyncio
async def test_by_email_ignores_case(operator, client: AsyncClient, test_user):
    resp = await client.get(URL, params={"email": test_user.email.upper()}, headers=operator)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_an_account_with_no_limits_row_is_unbounded(operator, client: AsyncClient, test_user):
    body = (await client.get(URL, params={"user_id": str(test_user.id)}, headers=operator)).json()
    assert {u["counter"]: u["maximum"] for u in body["usage"]} == dict.fromkeys(quota_service.COUNTERS)


@pytest.mark.asyncio
async def test_an_unknown_account_is_404(operator, client: AsyncClient):
    assert (await client.get(URL, params={"user_id": str(uuid.uuid4())}, headers=operator)).status_code == 404
    assert (await client.get(URL, params={"email": "nobody@example.com"}, headers=operator)).status_code == 404


@pytest.mark.asyncio
async def test_a_removed_account_is_404_and_an_inactive_one_is_not(operator, client: AsyncClient, session):
    # Removal keeps a closed shell when the person authored kept rows (ADR 0047).
    shell = await _user(session, "shell@example.com")
    shell.email = f"removed-{shell.id}@removed.invalid"
    shell.is_active = False
    paused = await _user(session, "paused@example.com")
    paused.is_active = False
    await session.commit()
    assert (await client.get(URL, params={"user_id": str(shell.id)}, headers=operator)).status_code == 404
    # Its address is reserved (RFC 2606) and refused as an email before any lookup.
    assert (await client.get(URL, params={"email": shell.email}, headers=operator)).status_code == 422
    resp = await client.get(URL, params={"user_id": str(paused.id)}, headers=operator)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_exactly_one_key_is_asked_for(operator, client: AsyncClient, test_user):
    assert (await client.get(URL, headers=operator)).status_code == 422
    both = {"user_id": str(test_user.id), "email": test_user.email}
    assert (await client.get(URL, params=both, headers=operator)).status_code == 422
    with_reference = {"email": test_user.email, "external_reference": "crm-0042"}
    assert (await client.get(URL, params=with_reference, headers=operator)).status_code == 422


OPEN = {
    "email": "ana@example.com",
    "external_reference": "crm-0042",
    "language": "pt-BR",
    "workspaces": [{"name": "Casa", "kind": "personal", "default_currency": "BRL"}],
    "quotas": {"max_personal_workspaces": 1, "max_business_workspaces": 1, "max_bank_connections": 3},
}


@pytest.mark.asyncio
async def test_an_opened_account_is_found_by_its_reference_with_no_password_yet(
    operator, client: AsyncClient, clean_db,
):
    opened = await client.post(URL, json=OPEN, headers=operator)
    assert opened.status_code == 201, opened.text

    resp = await client.get(URL, params={"external_reference": "crm-0042"}, headers=operator)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == opened.json()["user_id"]
    assert body["email"] == "ana@example.com"
    assert body["external_reference"] == "crm-0042"
    # Born with a password nobody knows: not set until the person picks one.
    assert body["has_password"] is False
    assert [w["name"] for w in body["workspaces"]] == ["Casa"]
    assert {u["counter"]: u["maximum"] for u in body["usage"]}["max_bank_connections"] == 3


@pytest.mark.asyncio
async def test_an_unknown_reference_is_404(operator, client: AsyncClient, test_user):
    resp = await client.get(URL, params={"external_reference": "crm-none"}, headers=operator)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_nothing_from_inside_a_workspace_reaches_the_answer(
    operator, client: AsyncClient, session: AsyncSession, test_user, test_workspace,
):
    await _held(session, test_user, test_workspace)
    resp = await client.get(URL, params={"user_id": str(test_user.id)}, headers=operator)
    text = resp.text
    for leaked in ("Checking Secret Name", "12345", "Some Bank", "member@example.com", "BRL"):
        assert leaked not in text
    assert set(resp.json()) == {
        "id", "email", "external_reference", "has_password", "is_active", "workspaces", "usage",
    }
    assert all(set(w) == {"id", "name", "kind", "is_archived", "members"} for w in resp.json()["workspaces"])
