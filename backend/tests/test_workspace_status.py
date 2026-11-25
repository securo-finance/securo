"""Suspending a workspace stops every write and scheduled job, and nothing else."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.plugin_connection import PluginConnection, PluginConnectionRoutine
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate, GroupSettlementUpdate
from app.services import (
    group_service,
    recurring_transaction_service,
    settlement_service,
    workspace_service,
)
from app.services.asset_service import refresh_all_market_prices
from app.services.invoice_schedule_service import due_schedule_ids
from app.services.workspace_status_service import SUSPENDED_DETAIL, in_active_workspace, is_suspended
from app.tasks import asset_tasks, plugin_tasks, sync_tasks


@pytest.fixture
async def suspended(session, test_workspace):
    test_workspace.status = "suspended"
    await session.commit()
    return test_workspace


async def _set(client, headers, workspace_id, status):
    return await client.patch(
        f"/api/admin/workspaces/{workspace_id}/status", headers=headers, json={"status": status}
    )


def _category():
    return {"name": f"c-{uuid.uuid4().hex[:8]}", "type": "expense"}


# --- transitions -----------------------------------------------------------


async def test_a_workspace_starts_active(client, auth_headers, test_workspace):
    resp = await client.get("/api/workspaces/current", headers=auth_headers)
    assert resp.json()["status"] == "active"


async def test_suspend_then_reactivate(
    client, auth_headers, admin_auth_headers, test_workspace, test_categories
):
    resp = await _set(client, admin_auth_headers, test_workspace.id, "suspended")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"
    assert resp.json()["status_changed_at"] is not None

    refused = await client.post("/api/categories", headers=auth_headers, json=_category())
    assert refused.status_code == 403
    assert refused.json()["detail"] == SUSPENDED_DETAIL

    resp = await _set(client, admin_auth_headers, test_workspace.id, "active")
    assert resp.json()["status"] == "active"
    allowed = await client.post("/api/categories", headers=auth_headers, json=_category())
    assert allowed.status_code == 201, allowed.text


async def test_setting_the_same_status_keeps_its_date(client, admin_auth_headers, test_workspace):
    first = await _set(client, admin_auth_headers, test_workspace.id, "suspended")
    again = await _set(client, admin_auth_headers, test_workspace.id, "suspended")
    # SQLite gives the date back without its offset.
    dates = [r.json()["status_changed_at"].rstrip("Z") for r in (first, again)]
    assert dates[0] == dates[1]


async def test_an_unknown_status_is_refused(client, admin_auth_headers, test_workspace):
    assert (await _set(client, admin_auth_headers, test_workspace.id, "paused")).status_code == 422


async def test_an_unknown_or_archived_workspace_is_not_found(
    client, session, admin_auth_headers, test_workspace
):
    assert (await _set(client, admin_auth_headers, uuid.uuid4(), "suspended")).status_code == 404
    test_workspace.is_archived = True
    await session.commit()
    resp = await _set(client, admin_auth_headers, test_workspace.id, "suspended")
    assert resp.status_code == 404


async def test_only_an_administrator_changes_the_status(client, auth_headers, suspended):
    assert (await _set(client, auth_headers, suspended.id, "active")).status_code == 403
    resp = await client.get("/api/admin/workspaces", headers=auth_headers)
    assert resp.status_code == 403


async def test_the_owner_cannot_lift_it_through_the_workspace_route(
    client, session, auth_headers, suspended
):
    await client.patch(
        f"/api/workspaces/{suspended.id}", headers=auth_headers, json={"status": "active"}
    )
    assert await is_suspended(session, suspended.id)


async def test_the_list_finds_by_name_and_by_owner(
    client, admin_auth_headers, test_workspace, test_user
):
    for term in (test_workspace.name, test_user.email):
        resp = await client.get(
            "/api/admin/workspaces", params={"search": term}, headers=admin_auth_headers
        )
        assert resp.status_code == 200, resp.text
        found = [w for w in resp.json()["items"] if w["id"] == str(test_workspace.id)]
        assert found and found[0]["owners"] == [test_user.email]
        assert found[0]["status"] == "active"
    none = await client.get(
        "/api/admin/workspaces", params={"search": "100%_none"}, headers=admin_auth_headers
    )
    assert none.json() == {"items": [], "total": 0}


# --- what a suspended workspace keeps and loses ----------------------------


async def test_reading_and_exporting_still_work(client, auth_headers, suspended, test_account):
    for path in ("/api/transactions", "/api/accounts", "/api/categories", "/api/budgets"):
        resp = await client.get(path, headers=auth_headers)
        assert resp.status_code == 200, f"{path}: {resp.text}"
    export = await client.get("/api/export/backup", headers=auth_headers)
    assert export.status_code == 200
    assert export.content[:2] == b"PK"


async def test_a_viewer_is_still_told_about_the_role(client, viewer_auth_headers, suspended):
    resp = await client.post("/api/categories", headers=viewer_auth_headers, json=_category())
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"].lower()


async def test_settings_and_roster_cannot_grow(client, auth_headers, suspended, test_user):
    rename = await client.patch(
        f"/api/workspaces/{suspended.id}", headers=auth_headers, json={"name": "Renamed"}
    )
    invite = await client.post(
        f"/api/workspaces/{suspended.id}/members",
        headers=auth_headers,
        json={"email": "someone@example.com", "role": "editor", "password": "password123"},
    )
    role = await client.patch(
        f"/api/workspaces/{suspended.id}/members/{test_user.id}",
        headers=auth_headers,
        json={"role": "owner"},
    )
    for resp in (rename, invite, role):
        assert resp.status_code == 403
        assert resp.json()["detail"] == SUSPENDED_DETAIL


async def test_a_member_can_still_leave(
    client, session, auth_headers, viewer_auth_headers, suspended
):
    members = await client.get(f"/api/workspaces/{suspended.id}/members", headers=auth_headers)
    viewer = next(m for m in members.json() if m["role"] == "viewer")
    resp = await client.delete(
        f"/api/workspaces/{suspended.id}/members/{viewer['user_id']}", headers=viewer_auth_headers
    )
    assert resp.status_code == 204


async def test_another_workspace_is_untouched(client, auth_headers, suspended):
    created = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Other", "kind": "personal", "self_membership": True},
    )
    headers = {**auth_headers, "X-Workspace-Id": created.json()["id"]}
    resp = await client.post("/api/categories", headers=headers, json=_category())
    assert resp.status_code == 201, resp.text


async def test_an_external_agent_cannot_apply(session, test_user, suspended):
    from mcp_server.auth import CallContext, ToolAccessDenied
    from mcp_server.registry import call_tool
    import mcp_server.tools.proposals  # noqa: F401

    ctx = CallContext(user_id=test_user.id, workspace_id=suspended.id, external=True)
    with pytest.raises(ToolAccessDenied, match="suspended"):
        await call_tool(session, ctx, "propose_categorize", {"apply": True})
    assert ctx.can_write is False


async def _shared_group(session, owner, workspace):
    """A group of `workspace` whose payer acts from a workspace of their own."""
    payer = User(
        id=uuid.uuid4(), email="payer@example.com", hashed_password="x", is_active=True,
        is_verified=True, preferences={"currency_display": "BRL"},
    )
    session.add(payer)
    await session.flush()
    payer_ws = await workspace_service.create_personal_workspace_for_user(
        session, payer, commit=True
    )
    group = await group_service.create_group(
        session, workspace.id, owner.id, GroupCreate(name="Trip", default_currency="BRL")
    )
    me = await group_service.create_member(
        session, group.id, workspace.id, GroupMemberCreate(name="Me", is_self=True)
    )
    member = await group_service.create_member(
        session, group.id, workspace.id,
        GroupMemberCreate(name="Payer", linked_user_id=payer.id),
    )
    assert me is not None and member is not None
    data = GroupSettlementCreate(
        from_member_id=member.id, to_member_id=me.id,
        amount=Decimal("3.00"), currency="BRL", date=date.today(),
    )
    return group, payer, payer_ws, data


async def test_a_linked_member_cannot_settle_into_it(session, test_user, test_workspace):
    group, payer, payer_ws, data = await _shared_group(session, test_user, test_workspace)
    settlement = await settlement_service.create_settlement(
        session, group.id, payer_ws.id, payer.id, data
    )
    assert settlement is not None
    test_workspace.status = "suspended"
    await session.commit()
    where = (session, group.id, settlement.id, payer_ws.id, payer.id)
    for attempt in (
        settlement_service.create_settlement(session, group.id, payer_ws.id, payer.id, data),
        settlement_service.update_settlement(*where, GroupSettlementUpdate(amount=Decimal("4"))),
        settlement_service.delete_settlement(*where),
    ):
        with pytest.raises(HTTPException) as refused:
            await attempt
        assert refused.value.detail == SUSPENDED_DETAIL
    assert await settlement_service.list_settlements(
        session, group.id, payer_ws.id, payer.id
    ) == [settlement]


@pytest.mark.parametrize("status,picked", [("suspended", False), ("active", True)])
async def test_a_settlement_credit_skips_its_accounts(
    session, test_user, test_workspace, test_account, status, picked
):
    test_workspace.status = status
    test_account.type = "checking"
    await session.commit()
    account = await settlement_service._pick_default_account_for_user(session, test_user.id)
    assert (account is not None and account.id == test_account.id) is picked


# --- scheduled work --------------------------------------------------------


def _bank(user, workspace_id):
    return BankConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id,
        provider="test",
        external_id=uuid.uuid4().hex,
        institution_name="Test Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=None,
        created_at=datetime.now(timezone.utc),
    )


class _Engine:
    async def dispose(self):
        return None


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_scheduled_bank_sync(session, test_user, test_workspace, status, calls):
    test_workspace.status = status
    session.add(_bank(test_user, test_workspace.id))
    await session.commit()
    with (
        patch.object(sync_tasks, "_make_session_maker", lambda: (_Engine(), lambda: session)),
        patch.object(sync_tasks, "_sync_one", new_callable=AsyncMock, return_value="refreshed") as one,
    ):
        result = await sync_tasks._sync_all()
    assert one.await_count == calls
    assert result["synced"] == calls


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_a_sync_already_queued(session, test_user, test_workspace, status, calls):
    """Suspended between being picked up and run."""
    conn = _bank(test_user, test_workspace.id)
    session.add(conn)
    test_workspace.status = status
    await session.commit()
    with (
        patch.object(sync_tasks, "_make_session_maker", lambda: (_Engine(), lambda: session)),
        patch.object(
            sync_tasks.connection_service,
            "sync_connection",
            new_callable=AsyncMock,
            return_value=(conn, 0, "refreshed"),
        ) as sync,
    ):
        await sync_tasks._sync_one(lambda: session, conn.id, test_user.id)
        assert sync.await_count == calls
        await sync_tasks._sync_one_celery(str(conn.id), str(test_user.id))
        assert sync.await_count == calls * 2


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_plugin_sync(session, test_user, test_workspace, status, calls):
    connection = PluginConnection(
        id=uuid.uuid4(),
        plugin_id="demo",
        integration="demo",
        name="Demo",
        workspace_id=test_workspace.id,
        created_by=test_user.id,
        sync_minutes=15,
        status="active",
    )
    session.add(connection)
    test_workspace.status = status
    await session.commit()
    with (
        patch.object(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: session)),
        patch.object(plugin_tasks, "close_redis", new_callable=AsyncMock),
        patch.object(plugin_tasks.connections, "due", lambda c: True),
        patch.object(plugin_tasks.connections, "sync", new_callable=AsyncMock) as sync,
    ):
        assert await plugin_tasks.sync_due(session) == calls
        await plugin_tasks._sync_one(str(connection.id))
        assert sync.await_count == calls * 2


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_plugin_routines(session, test_user, test_workspace, status, calls):
    connection = PluginConnection(
        id=uuid.uuid4(),
        plugin_id="demo",
        integration="demo",
        name="Demo",
        workspace_id=test_workspace.id,
        created_by=test_user.id,
        status="active",
    )
    session.add(connection)
    await session.flush()
    session.add(
        PluginConnectionRoutine(connection_id=connection.id, routine="tidy", every_minutes=15)
    )
    test_workspace.status = status
    await session.commit()
    with (
        patch.object(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: session)),
        patch.object(plugin_tasks, "close_redis", new_callable=AsyncMock),
        patch.object(plugin_tasks.connections, "routine_due", lambda r, c: True),
        patch.object(
            plugin_tasks.connections, "run_routine", new_callable=AsyncMock, return_value="ran"
        ) as run,
    ):
        assert await plugin_tasks.run_due_routines(session) == calls
        await plugin_tasks._run_routines_of(str(connection.id))
        assert run.await_count == calls * 2


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_recurring_job(session, test_user, test_workspace, test_account, status, calls):
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Rent",
            amount=Decimal("10.00"),
            currency=test_account.currency,
            type="debit",
            frequency="yearly",
            start_date=date.today() - timedelta(days=1),
            next_occurrence=date.today() - timedelta(days=1),
            auto_generate=True,
            is_active=True,
        )
    )
    test_workspace.status = status
    await session.commit()
    assert await recurring_transaction_service.generate_pending(session, test_user.id) == calls
    count = await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.description == "Rent")
    )
    assert count == calls


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_invoice_job(session, test_user, test_workspace, status, calls):
    """The statement the job runs, over a schedule row of this workspace."""
    from app.models.invoice_schedule import InvoiceSchedule
    from app.models.payee import Payee

    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="ACME")
    session.add(payee)
    await session.flush()
    session.add(
        InvoiceSchedule(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            payee_id=payee.id,
            status="active",
            origin="local",
            currency="BRL",
            name="Retainer",
            frequency="monthly",
            start_date=date.today(),
        )
    )
    test_workspace.status = status
    await session.commit()
    assert len(await due_schedule_ids(session)) == calls


def _asset(user, workspace_id, **kw):
    return Asset(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace_id, name="A", type="other",
        currency="BRL", **kw,
    )


@pytest.mark.parametrize("status,calls", [("suspended", 0), ("active", 1)])
async def test_the_growth_job(session, test_user, test_workspace, status, calls):
    asset = _asset(
        test_user, test_workspace.id, valuation_method="growth_rule", growth_type="absolute",
        growth_rate=Decimal("1"), growth_frequency="yearly",
    )
    session.add(asset)
    await session.flush()
    session.add(
        AssetValue(asset_id=asset.id, amount=Decimal("5"), date=date.today() - timedelta(days=400), source="manual")
    )
    test_workspace.status = status
    await session.commit()
    with patch.object(asset_tasks, "_make_session_maker", lambda: (_Engine(), lambda: session)):
        assert await asset_tasks._apply_growth_rules() == calls


@pytest.mark.parametrize("status,asked", [("suspended", 0), ("active", 1)])
async def test_the_price_job(session, test_user, test_workspace, status, asked):
    session.add(_asset(test_user, test_workspace.id, valuation_method="market_price", ticker="TEST"))
    test_workspace.status = status
    await session.commit()
    provider = AsyncMock()
    provider.get_latest_closes.return_value = {}
    provider.get_quote.return_value = None
    await refresh_all_market_prices(session, market_provider=provider)
    assert provider.get_latest_closes.await_count == asked


async def test_a_row_without_a_workspace_is_not_dropped(session):
    from sqlalchemy import Uuid, literal

    assert await session.scalar(select(in_active_workspace(literal(None, Uuid())))) in (True, 1)

