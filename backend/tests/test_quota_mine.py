"""An account reads its own usage limits (ADR 0049, #1972).

`GET /api/quotas/mine` answers the caller's bounded counters with the count
behind each, so the app can show them and explain a `quota_exceeded`. It lists
nothing without `MANAGED_INSTANCE` or without limits, never names an
unbounded counter, and carries the operator's link only when one is set.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.workspace import Workspace
from app.services import quota_service

URL = "/api/quotas/mine"


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


def _connection(workspace: Workspace, user_id: uuid.UUID) -> BankConnection:
    return BankConnection(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user_id, provider="pluggy",
        external_id=uuid.uuid4().hex, institution_name="Bank", credentials={}, status="active",
    )


@pytest.mark.asyncio
async def test_off_without_the_switch(client: AsyncClient, session: AsyncSession, test_user, auth_headers):
    await quota_service.set_quota(session, test_user.id, {"max_personal_workspaces": 1})
    await session.commit()

    resp = await client.get(URL, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enforced": False, "info_url": None, "counters": []}


@pytest.mark.asyncio
async def test_no_limits_lists_nothing(managed, client: AsyncClient, auth_headers):
    resp = await client.get(URL, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enforced": True, "info_url": None, "counters": []}


@pytest.mark.asyncio
async def test_lists_bounded_counters_with_their_count(
    managed, client: AsyncClient, session: AsyncSession, test_user, test_workspace, auth_headers,
):
    session.add(_connection(test_workspace, test_user.id))
    await quota_service.set_quota(
        session, test_user.id, {"max_personal_workspaces": 2, "max_bank_connections": 1},
    )
    await session.commit()

    resp = await client.get(URL, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["counters"] == [
        {"counter": "max_personal_workspaces", "current": 1, "maximum": 2},
        {"counter": "max_bank_connections", "current": 1, "maximum": 1},
    ]


@pytest.mark.asyncio
async def test_operator_defaults_hold_for_an_account_without_a_row(
    managed, client: AsyncClient, session: AsyncSession, auth_headers,
):
    await quota_service.set_defaults(session, {"max_business_workspaces": 0})
    await session.commit()

    resp = await client.get(URL, headers=auth_headers)

    assert resp.json()["counters"] == [
        {"counter": "max_business_workspaces", "current": 0, "maximum": 0},
    ]


@pytest.mark.asyncio
async def test_invited_member_sees_only_their_own_numbers(
    managed, client: AsyncClient, session: AsyncSession, test_user, test_workspace, viewer_auth_headers,
):
    """The owner's connection fills the owner's number, never the invited viewer's."""
    session.add(_connection(test_workspace, test_user.id))
    await quota_service.set_quota(session, test_user.id, {"max_bank_connections": 1})
    await session.commit()

    resp = await client.get(URL, headers=viewer_auth_headers)

    assert resp.json()["counters"] == []


@pytest.mark.asyncio
async def test_members_counter_reads_the_fullest_workspace(
    managed, client: AsyncClient, session: AsyncSession, test_user, viewer_auth_headers, auth_headers,
):
    await quota_service.set_quota(session, test_user.id, {"max_members_per_workspace": 3})
    await session.commit()

    resp = await client.get(URL, headers=auth_headers)

    assert resp.json()["counters"] == [
        {"counter": "max_members_per_workspace", "current": 1, "maximum": 3},
    ]


@pytest.mark.asyncio
async def test_carries_the_operator_link_only_when_set(managed, monkeypatch, client: AsyncClient, auth_headers):
    monkeypatch.setattr(get_settings(), "quota_info_url", "/help/limits")

    resp = await client.get(URL, headers=auth_headers)

    assert resp.json()["info_url"] == "/help/limits"


@pytest.mark.asyncio
async def test_needs_a_signed_in_account(client: AsyncClient):
    resp = await client.get(URL)

    assert resp.status_code == 401
