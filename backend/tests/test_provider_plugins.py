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


def test_complete_feed_is_off_unless_declared():
    keys, _ = parse_manifest(_provider_manifest()).contributes.integrations
    assert keys.complete_feed is False
    keys, _ = parse_manifest(_provider_manifest(complete_feed=True)).contributes.integrations
    assert keys.complete_feed is True


def test_complete_feed_needs_transactions():
    with pytest.raises(ValidationError):
        parse_manifest(_provider_manifest(provides=["accounts"], complete_feed=True))


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
from app.models.account_reserved_balance import AccountReservedBalance  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.models.bank_connection import BankConnection  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.plugins.contract import PayloadError, parse_payload  # noqa: E402
from app.services import account_service  # noqa: E402
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
                      "balance": "500.00", "currency": "USD", "institution_name": "Example Broker",
                      "transactions": transactions}],
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


def payload_with_reserved(reserved: Any) -> dict[str, Any]:
    data = payload_data()
    data["accounts"][0]["reserved_balances"] = reserved
    return data


#: Reserves kept in the account's own currency (USD), plus one in another (BRL).
POCKETS = [
    {"external_id": "emergency-fund", "name": "Emergency fund", "currency": "USD", "amount": "0"},
    {"external_id": "round-up", "name": "Round-up savings", "currency": "USD", "amount": "2000"},
    {"external_id": "brl-pocket", "name": "Poupanca", "currency": "BRL", "amount": "75"},
]


async def _reserved_rows(session: AsyncSession, bank_connection) -> list[AccountReservedBalance]:
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    rows = await _rows(session, AccountReservedBalance, account_id=account.id)
    return sorted(rows, key=lambda row: row.external_id)


@pytest.mark.asyncio
async def test_reserved_balances_from_a_payload_are_stored_and_never_summed(
    session: AsyncSession, bank_connection, test_user, test_workspace
):
    """ADR 0022 through the plugin path: kept under the account, out of every total."""
    _, _, outcome = await _pipeline(session, bank_connection, payload_with_reserved(POCKETS), test_user)
    assert outcome == "refreshed"

    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    rows = await _reserved_rows(session, bank_connection)
    assert [(r.external_id, r.name, r.currency, r.amount) for r in rows] == [
        ("brl-pocket", "Poupanca", "BRL", Decimal("75")),
        ("emergency-fund", "Emergency fund", "USD", Decimal("0")),
        ("round-up", "Round-up savings", "USD", Decimal("2000")),
    ]
    assert {r.workspace_id for r in rows} == {test_workspace.id}

    # The balance stays the one the plugin reported.
    assert account.balance == Decimal("500.00")
    await session.refresh(account, ["reserved_balances"])
    display = account_service.serialize_account(account, None, None)
    assert sorted(r["external_id"] for r in display["reserved_balances"]) == [
        "brl-pocket", "emergency-fund", "round-up",
    ]
    # Same-currency subtotal only: the BRL pocket never adds to a USD account.
    assert display["reserved_total"] == 2000.0
    assert display["current_balance"] == 500.0


@pytest.mark.asyncio
async def test_reserved_balances_omitted_are_kept_and_an_empty_list_clears_them(
    session: AsyncSession, bank_connection, test_user
):
    await _pipeline(session, bank_connection, payload_with_reserved(POCKETS), test_user)
    assert len(await _reserved_rows(session, bank_connection)) == 3

    # The plugin didn't report the collection this run: what Securo stored stays.
    await _pipeline(session, bank_connection, payload_data(), test_user)
    assert len(await _reserved_rows(session, bank_connection)) == 3

    # An explicit empty list is authoritative: the plugin has none active.
    await _pipeline(session, bank_connection, payload_with_reserved([]), test_user)
    assert await _reserved_rows(session, bank_connection) == []


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
from app.models.workspace_plugin import WorkspacePlugin  # noqa: E402
from app.plugins import connections, vault  # noqa: E402
from tests.test_code_plugins import V1, _apply, _connect, _tarball  # noqa: E402
from unittest.mock import patch  # noqa: E402

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
    assert bank.institution_name == "Ledger" and bank.credentials == {}
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


@pytest.mark.parametrize("vouched", [True, False])
@pytest.mark.asyncio
async def test_the_sync_hands_on_whether_the_feed_is_complete(
    session: AsyncSession, ids, provider_catalog, plugin_runner, vouched
):
    provider_catalog.manifests[V1] = _provider_manifest(complete_feed=True) if vouched else _provider_manifest()
    connection = await _provider_connection(session, ids)
    seen = []

    async def capture(*args, provider=None, **kwargs):
        assert provider is not None
        seen.append(provider.complete_feed)

    with patch("app.services.connection_service.sync_connection", new=capture):
        await connections.sync(session, connection)
    assert seen == [vouched]


@pytest.mark.asyncio
async def test_an_update_during_the_call_does_not_vouch_for_the_old_reply(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    connection = await _provider_connection(session, ids)
    original_call = connections._call
    seen = []

    async def call_then_update(session, connection, method, params, record=None):
        result = await original_call(session, connection, method, params, record)
        installed = await session.scalar(
            select(WorkspacePlugin).where(
                WorkspacePlugin.workspace_id == connection.workspace_id,
                WorkspacePlugin.plugin_id == connection.plugin_id,
            )
        )
        installed.manifest = _provider_manifest(complete_feed=True)
        await session.commit()
        return result

    async def capture(*args, provider=None, **kwargs):
        seen.append(provider.complete_feed)

    with (
        patch.object(connections, "_call", new=call_then_update),
        patch("app.services.connection_service.sync_connection", new=capture),
    ):
        await connections.sync(session, connection)
    assert seen == [False]


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
    assert "invalid data from the plugin" in (connection.last_error or "")
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
    assert "slow down" in (connection.last_error or "")
    assert not connections.due(connection)
    assert (await _bank(session, connection)).status == "active"


@pytest.mark.asyncio
async def test_a_provider_without_its_bank_connection_fails_and_keeps_the_cursor(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    connection = await _provider_connection(session, ids)
    connection.bank_connection_id = None
    await session.commit()
    await connections.sync(session, connection)
    assert (connection.status, connection.state) == ("error", {})
    assert "bank connection is gone" in (connection.last_error or "")
    assert await _rows(session, Account) == []


@pytest.mark.asyncio
async def test_a_failed_write_is_retried_from_the_same_cursor(
    session: AsyncSession, ids, provider_catalog, plugin_runner, monkeypatch
):
    connection = await _provider_connection(session, ids)

    async def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(connection_service, "sync_connection", broken)
    await connections.sync(session, connection)
    failed = await session.get(PluginConnection, connection.id)
    assert failed is not None and failed.status == "error" and failed.state == {}
    message = failed.last_error or ""
    assert "RuntimeError" in message and "database went away" not in message


# --- Disconnecting, removing and updating ------------------------------------------

from httpx import AsyncClient  # noqa: E402

from tests.test_code_plugins import V2  # noqa: E402


def _publish(catalog, commit: str, change) -> None:
    manifest = copy.deepcopy(catalog.manifests[catalog.commit])
    change(manifest)
    catalog.manifests[commit] = manifest
    catalog.archives[commit] = _tarball(PROVIDER_SOURCE + "\n# " + commit)
    catalog.commit = commit


async def _synced(session, ids) -> tuple[PluginConnection, uuid.UUID, uuid.UUID]:
    """A provider connection after one sync: its bank connection id and its asset id."""
    connection = await _provider_connection(session, ids)
    await connections.sync(session, connection)
    bank_id = connection.bank_connection_id
    assert bank_id is not None
    (asset,) = await _rows(session, Asset, connection_id=bank_id)
    (account,) = await _rows(session, Account, connection_id=bank_id)
    # While synced, the account shows under its institution's name.
    assert account.display_name == "Example Broker"
    return connection, bank_id, asset.id


async def _disconnected_like_a_bank(session, bank_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    session.expire_all()
    assert await session.get(BankConnection, bank_id) is None
    assert await _rows(session, Account, connection_id=bank_id) == []
    asset = await session.get(Asset, asset_id)
    # Archived and kept, as a native disconnect leaves it (Postgres also clears its connection_id).
    assert asset is not None and asset.is_archived


@pytest.mark.asyncio
async def test_removing_the_connection_disconnects_its_bank(session: AsyncSession, ids, provider_catalog, plugin_runner):
    connection, bank_id, asset_id = await _synced(session, ids)
    await connections.delete(session, connection)
    await _disconnected_like_a_bank(session, bank_id, asset_id)


async def _unlinked_keeping_records(session, bank_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    """The bank connection is gone, but its accounts and transactions stay as manual ones."""
    session.expire_all()
    assert await session.get(BankConnection, bank_id) is None
    (account,) = (await session.scalars(select(Account).where(Account.external_id == "A1:USD"))).all()
    assert account.connection_id is None
    # Named after itself again, not after the institution the sync showed.
    assert account.display_name is None and account.name == "Broker Cash (USD)"
    synced = await _rows(session, Transaction, account_id=account.id)
    assert sorted(t.external_id for t in synced) == ["t1", "t2"]
    # Its balance carries over: the opening balance makes the transactions add up to it.
    detail = await account_service.get_account_summary(session, account.id, account.workspace_id)
    assert detail is not None and Decimal(str(detail["current_balance"])) == Decimal("500.00")
    asset = await session.get(Asset, asset_id)
    assert asset is not None and asset.is_archived


@pytest.mark.asyncio
async def test_removing_the_plugin_keeps_what_its_banks_synced(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    _, bank_id, asset_id = await _synced(session, ids)
    plan = await service.plan(session, ids[0], PLUGIN, "remove", lang="en")
    assert plan.code is not None and plan.code.unlinks == 1
    await _apply(session, ids[0], ids[1], "remove")
    await _unlinked_keeping_records(session, bank_id, asset_id)
    assert await session.scalar(select(PluginConnection.id)) is None


@pytest.mark.asyncio
async def test_an_update_that_stops_providing_removes_the_connection_but_keeps_the_records(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    _, bank_id, asset_id = await _synced(session, ids)
    _publish(provider_catalog, V2, lambda m: m["contributes"]["integrations"][0].pop("provides"))
    plan = await service.plan(session, ids[0], PLUGIN, "update", lang="en")
    assert plan.code is not None and plan.code.unlinks == 1
    await _apply(session, ids[0], ids[1], "update")
    await _unlinked_keeping_records(session, bank_id, asset_id)
    assert await session.scalar(select(PluginConnection.id)) is None


@pytest.mark.asyncio
async def test_an_update_that_keeps_providing_unlinks_nothing(
    session: AsyncSession, ids, provider_catalog, plugin_runner
):
    await _synced(session, ids)
    _publish(provider_catalog, V2, lambda m: m.update(version="1.1.0"))
    plan = await service.plan(session, ids[0], PLUGIN, "update", lang="en")
    assert plan.code is not None and plan.code.unlinks == 0


@pytest.mark.asyncio
async def test_an_update_that_starts_providing_adds_the_bank_connection(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    assert connection.bank_connection_id is None
    _publish(catalog, V2, lambda m: m["contributes"]["integrations"][0].update(provides=["accounts"]))
    await _apply(session, workspace_id, user_id, "update")
    await session.refresh(connection)
    bank = await _bank(session, connection)
    assert bank.provider == provider_name(PLUGIN)


@pytest.mark.asyncio
async def test_bank_connections_api_syncs_and_disconnects_through_the_plugin(
    client: AsyncClient, auth_headers: dict, provider_catalog, plugin_runner, monkeypatch
):
    monkeypatch.setattr("app.api.plugins._queue_sync", lambda connection_id: None)
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 200
    created = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "keys", "name": "Main", "values": {"api_key": "sk_live_9"}},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text

    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    assert bank["provider"] == provider_name(PLUGIN) and bank["display_name"] == "Main"
    synced = await client.post(f"/api/connections/{bank['id']}/sync", headers=auth_headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["sync_outcome"] == "refreshed" and synced.json()["last_sync_at"]
    accounts = (await client.get("/api/accounts", headers=auth_headers)).json()
    assert [a["name"] for a in accounts if a.get("connection_id") == bank["id"]] == ["Broker Cash (USD)"]

    assert (await client.delete(f"/api/connections/{bank['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/connections", headers=auth_headers)).json() == []
    assert (await client.get("/api/plugins/connections", headers=auth_headers)).json() == []


@pytest.mark.asyncio
async def test_bank_connections_api_reports_a_plugin_that_needs_sign_in(
    client: AsyncClient, auth_headers: dict, provider_catalog, plugin_runner, monkeypatch
):
    monkeypatch.setattr("app.api.plugins._queue_sync", lambda connection_id: None)
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)
    await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "keys", "values": {"api_key": "k", "account": "expired"}},
        headers=auth_headers,
    )
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    synced = await client.post(f"/api/connections/{bank['id']}/sync", headers=auth_headers)
    assert synced.status_code == 410
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    assert bank["status"] == "expired"

    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    off = await client.post(f"/api/connections/{bank['id']}/sync", headers=auth_headers)
    assert off.status_code == 503 and "turned off" in off.json()["detail"]


# --- Managing a plugin's connection from Bank Connections ---------------------------------


async def _api_connected(client: AsyncClient, auth_headers: dict, values: dict) -> dict:
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 200
    created = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "keys", "name": "Main", "values": values},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["connection"]


@pytest.mark.asyncio
async def test_bank_connections_api_says_when_the_service_throttled_the_sync(
    client: AsyncClient, auth_headers: dict, provider_catalog, plugin_runner, monkeypatch
):
    monkeypatch.setattr("app.api.plugins._queue_sync", lambda connection_id: None)
    await _api_connected(client, auth_headers, {"api_key": "k", "account": "limited"})
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    synced = await client.post(f"/api/connections/{bank['id']}/sync", headers=auth_headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["sync_outcome"] == "rate_limited" and synced.json()["status"] == "active"


@pytest.mark.asyncio
async def test_bank_connections_say_which_plugin_syncs_them(
    client: AsyncClient, auth_headers: dict, provider_catalog, plugin_runner, monkeypatch
):
    monkeypatch.setattr("app.api.plugins._queue_sync", lambda connection_id: None)
    connection = await _api_connected(client, auth_headers, {"api_key": "sk_live_1"})
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    assert (bank["plugin_connection_id"], bank["plugin_id"], bank["integration"]) == (connection["id"], PLUGIN, "keys")


@pytest.mark.asyncio
async def test_new_values_keep_blank_secrets_and_sync_afresh(
    client: AsyncClient, auth_headers: dict, provider_catalog, plugin_runner, monkeypatch, session: AsyncSession
):
    queued: list = []
    monkeypatch.setattr("app.api.plugins._queue_sync", queued.append)
    connection = await _api_connected(client, auth_headers, {"api_key": "sk_live_1", "account": "expired"})
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    assert (await client.post(f"/api/connections/{bank['id']}/sync", headers=auth_headers)).status_code == 410

    updated = await client.put(
        f"/api/plugins/connections/{connection['id']}/values",
        json={"name": "Renamed", "values": {"api_key": "", "account": "ok"}},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["status"], updated.json()["config"], updated.json()["name"]) == ("pending", {"account": "ok"}, "Renamed")
    stored = await session.get(PluginConnection, uuid.UUID(connection["id"]))
    assert stored is not None and vault.unseal(stored.secrets)["api_key"] == "sk_live_1"
    (bank,) = (await client.get("/api/connections", headers=auth_headers)).json()
    assert (bank["status"], bank["display_name"]) == ("active", "Renamed")
    assert len(queued) == 2  # the first sync, and the one after the new values

    bad = await client.put(
        f"/api/plugins/connections/{connection['id']}/values", json={"values": {"nope": "x"}}, headers=auth_headers
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_the_connection_shows_the_institution_logo_its_accounts_name(
    session: AsyncSession, bank_connection, test_user
):
    data = payload_data()
    data["accounts"][0]["institution_logo"] = "broker"
    payload = parse_payload(data, ALL_KINDS, {"broker"})
    assert payload is not None
    logo = "/api/plugins/logos/" + "a" * 64
    provider = PluginPayloadProvider(provider_name(PLUGIN), payload, {"broker": logo})
    await connection_service.sync_connection(
        session, bank_connection.id, bank_connection.workspace_id, test_user.id, provider=provider
    )
    await session.refresh(bank_connection)
    assert bank_connection.logo_url == logo


def test_an_account_can_only_name_a_logo_the_manifest_ships():
    data = payload_data()
    data["accounts"][0]["institution_logo"] = "elsewhere"
    with pytest.raises(PayloadError, match="logos have no such key"):
        parse_payload(data, ALL_KINDS, {"broker"})
    data["accounts"][0].pop("institution_logo")
    data["accounts"][0]["institution_logo_url"] = "https://tracker.example/pixel.png"
    with pytest.raises(PayloadError, match="institution_logo_url"):
        parse_payload(data, ALL_KINDS, {"broker"})



@pytest.mark.asyncio
async def test_new_values_keep_what_the_plugin_stored_and_wait_for_a_sync(
    session: AsyncSession, ids, provider_catalog, plugin_runner, sync_locks
):
    connection = await _provider_connection(session, ids)
    secrets = vault.unseal(connection.secrets)
    connection.secrets = vault.seal({**secrets, "refresh_token": "kept-by-the-plugin"})
    await session.commit()

    sync_locks.add(f"plugin-sync:{connection.id}")  # a sync is running
    with pytest.raises(connections.PluginConnectionError, match="syncing"):
        await connections.update_values(session, connection, name=None, values={"account": "ok"})
    sync_locks.clear()

    await connections.update_values(session, connection, name=None, values={"api_key": "sk_live_2"})
    stored = vault.unseal(connection.secrets)
    assert stored["api_key"] == "sk_live_2" and stored["refresh_token"] == "kept-by-the-plugin"


@pytest.mark.asyncio
async def test_new_values_clear_the_reason_the_old_ones_left_on_the_bank(
    session: AsyncSession, ids, provider_catalog, plugin_runner, sync_locks
):
    """Issue #1405: the bank card must not keep the old credentials' notice
    until a sync that writes records."""
    connection = await _provider_connection(session, ids)
    bank = await _bank(session, connection)
    failure = {"code": "consent_expired", "at": datetime.now(timezone.utc).isoformat(), "detail": None}
    bank.settings = {**(bank.settings or {}), "last_sync_failure": failure}
    await session.commit()

    await connections.update_values(session, connection, name=None, values={"api_key": "sk_live_2"})

    bank = await _bank(session, connection)
    assert bank.last_sync_failure is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values", [{}, {"api_key": "sk_live_1"}, {"api_key": "  "}], ids=["rename", "same-key", "blank-key"]
)
async def test_a_rename_or_the_same_values_keep_the_reason_on_the_bank(
    session: AsyncSession, ids, provider_catalog, plugin_runner, sync_locks, values
):
    """Nothing about the credentials changed, so the bank's notice still holds."""
    connection = await _provider_connection(session, ids)
    bank = await _bank(session, connection)
    failure = {"code": "consent_expired", "at": datetime.now(timezone.utc).isoformat(), "detail": None}
    bank.settings = {**(bank.settings or {}), "last_sync_failure": failure}
    await session.commit()

    await connections.update_values(session, connection, name="Renamed", values=values)

    bank = await _bank(session, connection)
    assert bank.display_name == "Renamed"
    assert bank.last_sync_failure is not None
    assert bank.last_sync_failure["code"] == "consent_expired"


@pytest.mark.asyncio
async def test_an_oauth_return_without_new_secrets_keeps_the_reason_on_the_bank(
    session: AsyncSession, ids, provider_catalog, plugin_runner, sync_locks, monkeypatch
):
    connection = await _provider_connection(session, ids)
    bank = await _bank(session, connection)
    failure = {"code": "consent_expired", "at": datetime.now(timezone.utc).isoformat(), "detail": None}
    bank.settings = {**(bank.settings or {}), "last_sync_failure": failure}
    await session.commit()
    workspace_id, user_id = ids

    async def consume(state):
        return {
            "kind": "plugin",
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "connection_id": str(connection.id),
        }

    async def exchange(session, connection, method, params):
        return {"secrets": {}}, vault.unseal(connection.secrets)

    monkeypatch.setattr(connections.oauth_state, "consume_state", consume)
    monkeypatch.setattr(connections, "_call", exchange)
    await connections.finish_oauth(session, workspace_id=workspace_id, user_id=user_id, code="c", state="s")
    bank = await _bank(session, connection)
    assert bank.last_sync_failure is not None

    async def signed_in(session, connection, method, params):
        return {"secrets": {"access_token": "t"}}, vault.unseal(connection.secrets)

    monkeypatch.setattr(connections, "_call", signed_in)
    await connections.finish_oauth(session, workspace_id=workspace_id, user_id=user_id, code="c", state="s")
    bank = await _bank(session, connection)
    assert bank.last_sync_failure is None
