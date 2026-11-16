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
from datetime import datetime, timezone  # noqa: E402
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
