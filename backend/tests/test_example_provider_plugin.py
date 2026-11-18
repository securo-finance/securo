"""The example provider plugin in `examples/plugins/demo-bank` keeps working.

Its manifest and code are installed on the real runner, as in
`test_provider_plugins`, and its sync goes through `connections.sync` and the
bank sync pipeline. When the contract or the pipeline changes in a way the
example no longer fits, this fails instead of the example going stale.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import tarfile
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.plugins import connections
from app.plugins.manifest import parse_manifest
from tests import test_code_plugins as code_plugins
from tests.test_code_plugins import PLUGIN, REPO, V1, _apply, _connect
from tests.test_provider_plugins import _bank, _rows

# The same fake catalog, in-process runner and in-memory sync locks.
catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
sync_locks = code_plugins.sync_locks
ids = code_plugins.ids

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "demo-bank"


def _manifest() -> dict:
    return json.loads((EXAMPLE / "securo-plugin.json").read_text())


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_bank_example", EXAMPLE / "demo_bank.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive() -> bytes:
    """The example folder as a release archive (one top folder, as GitHub builds them)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(EXAMPLE.iterdir()):
            if path.is_file():
                tar.add(path, arcname=f"your-name-securo-demo-bank-abc/{path.name}")
    return buffer.getvalue()


@pytest.fixture
def demo_catalog(catalog):
    # The fake catalog serves one plugin id; the example's code and manifest go under it.
    catalog.manifests[V1] = {**_manifest(), "id": PLUGIN, "repository": REPO}
    catalog.archives[V1] = _archive()
    return catalog


async def _connection(session, ids, access_code="1234"):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(
        session, workspace_id, user_id, integration="demo", customer="Alex", access_code=access_code
    )
    return connection


def _expected() -> dict:
    """What the example returns on a first sync, read from its own code."""
    context = SimpleNamespace(secrets={"access_code": "1234"}, config={"customer": "Alex"})
    return _module().plugin.sync({"state": {}, "since": None}, context)["data"]


def test_the_example_manifest_is_a_provider():
    manifest = parse_manifest(_manifest())
    (integration,) = manifest.contributes.integrations
    assert manifest.has_code and integration.is_provider
    assert integration.provides == ["accounts", "transactions", "bills", "holdings", "trades"]
    assert manifest.network == []


@pytest.mark.asyncio
async def test_the_example_syncs_through_the_bank_pipeline(session: AsyncSession, ids, demo_catalog, plugin_runner):
    connection = await _connection(session, ids)
    await connections.sync(session, connection)
    assert (connection.status, connection.last_error) == ("active", None)
    assert connection.state == {"last_sync": dt.date.today().isoformat()}

    expected = _expected()
    bank = await _bank(session, connection)
    assert bank.status == "active" and bank.last_sync_at is not None
    accounts = {a.external_id: a for a in await _rows(session, Account, connection_id=bank.id)}
    assert set(accounts) == {"demo-checking", "demo-card"}
    for data in expected["accounts"]:
        account = accounts[data["external_id"]]
        assert (account.name, account.type, account.currency) == (data["name"], data["type"], "USD")
        assert account.balance == Decimal(data["balance"])
        synced = {t.external_id: t for t in await _rows(session, Transaction, account_id=account.id)}
        assert set(synced) == {t["external_id"] for t in data["transactions"]}
        for record in data["transactions"]:
            assert synced[record["external_id"]].status == record["status"]

    (bill,) = await _rows(session, CreditCardBill, account_id=accounts["demo-card"].id)
    (bill_data,) = expected["accounts"][1]["bills"]
    assert bill.due_date.isoformat() == bill_data["due_date"]
    assert bill.total_amount == Decimal(bill_data["total_amount"])

    (asset,) = await _rows(session, Asset, connection_id=bank.id)
    assert asset.external_id.endswith(":fund-world") and asset.units == Decimal("12")
    trades = await _rows(session, AssetTransaction, asset_id=asset.id)
    assert sorted(t.external_id for t in trades) == ["fund-world-buy-1", "fund-world-buy-2"]


@pytest.mark.asyncio
async def test_syncing_the_example_again_adds_nothing(session: AsyncSession, ids, demo_catalog, plugin_runner):
    connection = await _connection(session, ids)
    await connections.sync(session, connection)
    bank = await _bank(session, connection)
    bank_id = bank.id
    account_ids = [a.id for a in await _rows(session, Account, connection_id=bank_id)]
    before = {i: len(await _rows(session, Transaction, account_id=i)) for i in account_ids}

    await connections.sync(session, connection)
    assert connection.status == "active"
    assert len(await _rows(session, Account, connection_id=bank_id)) == 2
    after = {i: len(await _rows(session, Transaction, account_id=i)) for i in account_ids}
    assert after == before
    assert len(await _rows(session, Asset, connection_id=bank_id)) == 1


@pytest.mark.asyncio
async def test_the_example_asks_to_reconnect_when_its_sign_in_expired(
    session: AsyncSession, ids, demo_catalog, plugin_runner
):
    connection = await _connection(session, ids, access_code="expired")
    await connections.sync(session, connection)
    assert connection.status == "needs_auth"
    assert (await _bank(session, connection)).status == "expired"


@pytest.mark.asyncio
async def test_the_example_rate_limited_is_no_failure(session: AsyncSession, ids, demo_catalog, plugin_runner):
    connection = await _connection(session, ids, access_code="busy")
    await connections.sync(session, connection)
    assert connection.status == "active" and connection.failures == 0
    assert connection.last_error is not None and connection.last_error.startswith("RateLimited")
    bank = await _bank(session, connection)
    assert await _rows(session, Account, connection_id=bank.id) == []
