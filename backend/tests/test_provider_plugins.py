"""Provider plugins: integrations that return records for the bank sync pipeline (#1461).

The runner is the real one, in process, as in `test_code_plugins`; the
plugin's `sync` returns `data` and Securo writes it as a connected bank.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.plugins import service
from app.plugins.manifest import parse_manifest
from tests import test_code_plugins as code_plugins
from tests.test_code_plugins import MANIFEST, PLUGIN

# The same fake catalog, in-process runner and in-memory sync locks.
catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
states = code_plugins.states
sync_locks = code_plugins.sync_locks
ids = code_plugins.ids


def _provider_manifest(**integration: Any) -> dict[str, Any]:
    manifest = copy.deepcopy(MANIFEST)
    manifest["contributes"]["integrations"][0].update(
        {"provides": ["accounts", "transactions", "holdings", "trades"], **integration}
    )
    return manifest


# --- The manifest -------------------------------------------------------------


def test_an_integration_declares_what_it_provides():
    manifest = parse_manifest(_provider_manifest())
    keys, signin = manifest.contributes.integrations
    assert keys.provides == ["accounts", "transactions", "holdings", "trades"]
    assert keys.is_provider
    assert signin.provides is None and not signin.is_provider


@pytest.mark.parametrize(
    "provides",
    [[], ["transactions"], ["bills"], ["trades"], ["accounts", "accounts"], ["accounts", "payees"]],
    ids=["empty", "transactions-without-accounts", "bills-without-accounts", "trades-without-holdings",
         "duplicate", "unknown-kind"],
)
def test_provides_must_hang_together(provides):
    with pytest.raises(ValidationError):
        parse_manifest(_provider_manifest(provides=provides))


def test_provides_and_api_scopes_can_be_declared_together():
    manifest = parse_manifest(_provider_manifest())
    assert manifest.api_scopes and manifest.contributes.integrations[0].provides


@pytest.mark.asyncio
async def test_the_consent_shows_what_an_integration_provides(session: AsyncSession, ids, catalog, plugin_runner):
    catalog.manifests[catalog.commit] = _provider_manifest()
    workspace_id, _ = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    assert plan.code is not None
    by_key = {i["key"]: i for i in plan.code.integrations}
    assert by_key["keys"]["provides"] == ["accounts", "transactions", "holdings", "trades"]
    assert by_key["signin"]["provides"] is None


# --- The payload as a provider --------------------------------------------------

import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models.account import Account  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.models.bank_connection import BankConnection  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.plugins.contract import parse_payload  # noqa: E402
from app.plugins.provider import PluginPayloadProvider, provider_name  # noqa: E402
from app.providers.base import ProviderNotConfiguredError  # noqa: E402
from app.services import connection_service  # noqa: E402

ALL_KINDS = ["accounts", "transactions", "holdings", "trades"]


def payload_data(*, transactions: Any = "default", holdings: Any = "default") -> dict[str, Any]:
    if transactions == "default":
        transactions = [
            {"external_id": "t1", "description": "Dividend", "amount": "12.34", "date": "2026-11-14", "type": "credit"},
            {"external_id": "t2", "description": "Fee", "amount": "1.00", "date": "2026-11-15", "type": "debit",
             "status": "pending"},
        ]
    if holdings == "default":
        holdings = [{"external_id": "A1:STK:1", "name": "Example ETF", "currency": "USD",
                     "current_value": "1000.00", "quantity": "10", "ticker": "EXMP"}]
    return {
        "contract": 1,
        "accounts": [{"external_id": "A1:USD", "name": "Broker Cash (USD)", "type": "investment",
                      "balance": "500.00", "currency": "USD", "transactions": transactions}],
        "holdings": holdings,
    }


def adapter(data: dict[str, Any]) -> PluginPayloadProvider:
    payload = parse_payload(data, ALL_KINDS)
    assert payload is not None
    return PluginPayloadProvider(provider_name(PLUGIN), payload)


@pytest.fixture
async def bank_connection(session: AsyncSession, test_user, test_workspace) -> BankConnection:
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        provider=provider_name(PLUGIN), external_id=str(uuid.uuid4()), institution_name="Ledger",
        credentials={}, settings={}, status="active", created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.commit()
    return connection


async def _pipeline(session, connection, data, user):
    return await connection_service.sync_connection(
        session, connection.id, connection.workspace_id, user.id, provider=adapter(data)
    )


async def _rows(session, model, **where):
    query = select(model)
    if model is Transaction:
        # Synced rows only, not the account's opening balance.
        query = query.where(Transaction.external_id.is_not(None))
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return (await session.execute(query)).scalars().all()


@pytest.mark.asyncio
async def test_a_payload_syncs_like_a_connected_bank(session: AsyncSession, bank_connection, test_user):
    _, count, outcome = await _pipeline(session, bank_connection, payload_data(), test_user)
    assert outcome == "refreshed"
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    assert account.external_id == "A1:USD" and account.currency == "USD"
    transactions = await _rows(session, Transaction, account_id=account.id)
    assert {t.external_id: t.status for t in transactions} == {"t1": "posted", "t2": "pending"}
    assert {t.source for t in transactions} == {"sync"}
    (asset,) = await _rows(session, Asset, connection_id=bank_connection.id)
    assert (asset.source, asset.external_id, asset.units) == (provider_name(PLUGIN), "A1:STK:1", Decimal("10"))
    assert bank_connection.last_sync_at is not None


@pytest.mark.asyncio
async def test_the_same_payload_twice_adds_nothing(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, payload_data(), test_user)
    await _pipeline(session, bank_connection, payload_data(), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    assert len(await _rows(session, Transaction, account_id=account.id)) == 2
    assert len(await _rows(session, Asset, connection_id=bank_connection.id)) == 1


@pytest.mark.asyncio
async def test_a_pending_transaction_settles(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, payload_data(), test_user)
    settled = payload_data()
    settled["accounts"][0]["transactions"][1]["status"] = "posted"
    await _pipeline(session, bank_connection, settled, test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    statuses = {t.external_id: t.status for t in await _rows(session, Transaction, account_id=account.id)}
    assert statuses == {"t1": "posted", "t2": "posted"}


@pytest.mark.asyncio
async def test_holdings_not_fetched_are_left_and_an_empty_list_archives(
    session: AsyncSession, bank_connection, test_user
):
    await _pipeline(session, bank_connection, payload_data(), test_user)
    await _pipeline(session, bank_connection, payload_data(holdings=None), test_user)
    (asset,) = await _rows(session, Asset, connection_id=bank_connection.id)
    assert not asset.is_archived
    await _pipeline(session, bank_connection, payload_data(holdings=[]), test_user)
    await session.refresh(asset)
    assert asset.is_archived


@pytest.mark.asyncio
async def test_transactions_not_fetched_are_a_partial_feed(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, payload_data(transactions=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    assert await _rows(session, Transaction, account_id=account.id) == []
    assert bank_connection.last_sync_at is None


@pytest.mark.asyncio
async def test_a_plugin_connection_is_never_synced_without_its_plugin(
    session: AsyncSession, bank_connection, test_user
):
    with pytest.raises(ProviderNotConfiguredError, match="synced by its plugin"):
        await connection_service.sync_connection(
            session, bank_connection.id, bank_connection.workspace_id, test_user.id
        )


@pytest.mark.asyncio
async def test_the_bank_sync_schedule_leaves_plugin_connections_to_their_plugin(
    session: AsyncSession, bank_connection, test_user, test_workspace, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.tasks import sync_tasks

    native = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="native", institution_name="Bank", credentials={"token": "x"}, settings={},
        status="active", created_at=datetime.now(timezone.utc),
    )
    session.add(native)
    await session.commit()
    engine = AsyncMock()
    monkeypatch.setattr(sync_tasks, "_make_session_maker", lambda: (engine, lambda: session))
    synced = AsyncMock(return_value="refreshed")
    monkeypatch.setattr(sync_tasks, "_sync_one", synced)
    await sync_tasks._sync_all()
    assert [call.args[1] for call in synced.await_args_list] == [native.id]


# --- Syncing a provider plugin through the runner ------------------------------------

import json  # noqa: E402

from app.models.plugin_connection import PluginConnection  # noqa: E402
from app.plugins import connections, vault  # noqa: E402
from tests.test_code_plugins import V1, _apply, _connect, _tarball  # noqa: E402

PROVIDER_SOURCE = (
    "import json\n"
    f"PAYLOAD = json.loads({json.dumps(json.dumps(payload_data()))})\n"
    '''
class Plugin:
    def sync(self, params, context):
        mode = context.config.get("account") or "ok"
        runs = params["state"].get("runs", 0)
        if mode == "limited":
            raise type("RateLimited", (Exception,), {})("slow down")
        if mode == "expired":
            raise type("NeedsAuth", (Exception,), {})("sign in again")
        state = {"runs": runs + 1, "since": params.get("since")}
        if mode == "pending":
            return {"state": state, "data": None}
        data = {"contract": 1, "accounts": [{"external_id": "x"}]} if mode == "invalid" else PAYLOAD
        return {"state": state, "secrets": {"refreshed": str(runs + 1)}, "data": data}

plugin = Plugin()
'''
)


@pytest.fixture
def provider_catalog(catalog):
    catalog.manifests[V1] = _provider_manifest()
    catalog.archives[V1] = _tarball(PROVIDER_SOURCE)
    return catalog


async def _provider_connection(session, ids, mode="ok") -> PluginConnection:
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    values = {"api_key": "sk_live_1"} if mode == "ok" else {"api_key": "sk_live_1", "account": mode}
    connection, _ = await _connect(session, workspace_id, user_id, **values)
    return connection


async def _bank(session, connection) -> BankConnection:
    bank = await session.get(BankConnection, connection.bank_connection_id)
    assert bank is not None
    await session.refresh(bank)
    return bank


@pytest.mark.asyncio
async def test_connecting_a_provider_integration_adds_a_bank_connection(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    connection = await _provider_connection(session, ids)
    bank = await _bank(session, connection)
    assert (bank.provider, bank.external_id, bank.status) == (provider_name(PLUGIN), str(connection.id), "active")
    assert bank.institution_name == "Ledger (API key)" and bank.credentials == {}
    assert bank.workspace_id == connection.workspace_id and bank.user_id == ids[1]


@pytest.mark.asyncio
async def test_a_sync_writes_the_records_and_keeps_the_cursor(session: AsyncSession, ids, provider_catalog, plugin_runner):
    connection = await _provider_connection(session, ids)
    await connections.sync(session, connection)
    assert (connection.status, connection.last_error, connection.state["runs"]) == ("active", None, 1)
    assert connection.state["since"] is None
    bank = await _bank(session, connection)
    assert bank.status == "active" and bank.last_sync_at is not None
    (account,) = await _rows(session, Account, connection_id=bank.id)
    account_id, first_sync = account.id, bank.last_sync_at
    assert len(await _rows(session, Transaction, account_id=account_id)) == 2
    assert len(await _rows(session, Asset, connection_id=bank.id)) == 1

    await connections.sync(session, connection)
    assert connection.state["runs"] == 2
    assert connection.state["since"] == (first_sync.date() - timedelta(days=14)).isoformat()
    assert len(await _rows(session, Transaction, account_id=account_id)) == 2
    assert vault.unseal(connection.secrets)["refreshed"] == "2"


@pytest.mark.asyncio
async def test_nothing_fetched_keeps_the_cursor_and_writes_nothing(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    connection = await _provider_connection(session, ids, "pending")
    await connections.sync(session, connection)
    assert (connection.status, connection.state["runs"]) == ("active", 1)
    bank = await _bank(session, connection)
    assert await _rows(session, Account, connection_id=bank.id) == []
    assert bank.last_sync_at is None


@pytest.mark.asyncio
async def test_invalid_data_is_a_failure_that_keeps_secrets_but_not_the_cursor(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    connection = await _provider_connection(session, ids, "invalid")
    await connections.sync(session, connection)
    assert connection.status == "error" and connection.failures == 1
    assert "invalid data from the plugin" in connection.last_error
    assert connection.state == {}
    assert vault.unseal(connection.secrets)["refreshed"] == "1"
    bank = await _bank(session, connection)
    assert bank.status == "error"
    assert await _rows(session, Account, connection_id=bank.id) == []


@pytest.mark.asyncio
async def test_a_signed_out_service_asks_to_reconnect(session: AsyncSession, ids, provider_catalog, plugin_runner):
    connection = await _provider_connection(session, ids, "expired")
    await connections.sync(session, connection)
    assert connection.status == "needs_auth"
    assert (await _bank(session, connection)).status == "expired"


@pytest.mark.asyncio
async def test_a_rate_limited_service_is_no_failure(session: AsyncSession, ids, provider_catalog, plugin_runner):
    connection = await _provider_connection(session, ids, "limited")
    await connections.sync(session, connection)
    assert (connection.status, connection.failures, connection.state) == ("active", 0, {})
    assert "slow down" in connection.last_error
    assert not connections.due(connection)
    assert (await _bank(session, connection)).status == "active"


@pytest.mark.asyncio
async def test_a_failed_write_is_retried_from_the_same_cursor(
    session: AsyncSession, ids, provider_catalog, plugin_runner, monkeypatch
):
    connection = await _provider_connection(session, ids)

    async def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(connection_service, "sync_connection", broken)
    await connections.sync(session, connection)
    connection = await session.get(PluginConnection, connection.id)
    assert connection.status == "error" and connection.state == {}
    assert "RuntimeError" in connection.last_error and "database went away" not in connection.last_error
