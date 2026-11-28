"""Provider plugins: integrations that return records for the bank sync pipeline (#1461).

The runner is the real one, in process, as in `test_code_plugins`; the
plugin's `sync` returns `data` and Securo writes it as a connected bank.
"""
from __future__ import annotations

import copy
from datetime import date
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
from app.models.payee import Payee  # noqa: E402
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
async def test_a_transaction_brings_its_notes(session: AsyncSession, bank_connection, test_user):
    """The bank's full purpose text goes to the notes: searched and matched by rules, not shown in the list."""
    data = payload_data()
    data["accounts"][0]["transactions"][0]["notes"] = "Ihr Einkauf bei Example Shop, order 123-4567890"
    await _pipeline(session, bank_connection, data, test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    notes = {t.external_id: t.notes for t in await _rows(session, Transaction, account_id=account.id)}
    assert notes == {"t1": "Ihr Einkauf bei Example Shop, order 123-4567890", "t2": None}


def _account_txns(*transactions: dict[str, Any]) -> dict[str, Any]:
    return payload_data(transactions=list(transactions), holdings=None)


def _txn(
    external_id: str, *, status: str = "posted", notes: str | None = None, amount: str = "12.34"
) -> dict[str, Any]:
    txn = {"external_id": external_id, "description": "Example Shop", "amount": amount,
           "date": "2026-11-14", "type": "debit", "status": status}
    if notes is not None:
        txn["notes"] = notes
    return txn


async def _only_row(session, connection) -> Transaction:
    (account,) = await _rows(session, Account, connection_id=connection.id)
    (row,) = await _rows(session, Transaction, account_id=account.id)
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("posted_id", ["p1", "p2"], ids=["same-id", "new-id"])
async def test_notes_reach_a_pending_row_when_it_posts(
    session: AsyncSession, bank_connection, test_user, posted_id
):
    """The bank often adds the purpose text only on booking, under the same id
    or as a twin with a new one: the notes reach the row the hold left."""
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", status="pending")), test_user)
    await _pipeline(session, bank_connection, _account_txns(_txn(posted_id, notes="Ihr Einkauf")), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.external_id, row.status, row.notes) == (posted_id, "posted", "Ihr Einkauf")


@pytest.mark.asyncio
async def test_a_users_notes_stay_and_the_providers_join_them(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", status="pending")), test_user)
    row = await _only_row(session, bank_connection)
    row.notes = "split with Alex"
    await session.commit()
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", notes="Ihr Einkauf")), test_user)
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", notes="Ihr Einkauf")), test_user)
    row = await _only_row(session, bank_connection)
    assert row.notes == "split with Alex Ihr Einkauf"


async def _complete_feed_sync(session, connection, data, user):
    payload = parse_payload(data, ALL_KINDS)
    assert payload is not None
    provider = PluginPayloadProvider(provider_name(PLUGIN), payload, complete_feed=True)
    return await connection_service.sync_connection(
        session, connection.id, connection.workspace_id, user.id, provider=provider
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [None, "typed over", "cleared"])
async def test_a_cancelled_hold_with_the_providers_notes_still_goes(
    session: AsyncSession, bank_connection, test_user, edit
):
    """Notes the plugin sent are not the user's work: a complete feed that
    drops the hold removes it. Notes the user changed (or cleared) keep it,
    also after a sync that still listed the hold with the plugin's notes."""
    hold = _txn("h1", status="pending", notes="Hotel deposit")
    await _complete_feed_sync(session, bank_connection, _account_txns(hold, _txn("k1", amount="99.00")), test_user)
    if edit is not None:
        row = next(r for r in await _rows(session, Transaction, external_id="h1"))
        row.notes = "Hotel deposit, refund due" if edit == "typed over" else None
        await session.commit()
    # A sync that still lists the hold, its notes unchanged, must not undo the edit.
    await _complete_feed_sync(session, bank_connection, _account_txns(hold, _txn("k1", amount="99.00")), test_user)

    await _complete_feed_sync(session, bank_connection, _account_txns(_txn("k1", amount="99.00")), test_user)

    kept = {r.external_id for r in await _rows(session, Transaction)}
    assert kept == ({"k1"} if edit is None else {"h1", "k1"})


@pytest.mark.asyncio
async def test_a_hold_whose_notes_grew_from_the_provider_still_goes(
    session: AsyncSession, bank_connection, test_user
):
    """Notes the plugin added on a later sync are still its own."""
    await _complete_feed_sync(
        session, bank_connection, _account_txns(_txn("h1", status="pending"), _txn("k1", amount="99.00")), test_user
    )
    await _complete_feed_sync(
        session, bank_connection,
        _account_txns(_txn("h1", status="pending", notes="Hotel deposit"), _txn("k1", amount="99.00")), test_user,
    )
    row = next(r for r in await _rows(session, Transaction, external_id="h1"))
    assert row.notes == "Hotel deposit"

    await _complete_feed_sync(session, bank_connection, _account_txns(_txn("k1", amount="99.00")), test_user)

    assert {r.external_id for r in await _rows(session, Transaction)} == {"k1"}


def test_notes_longer_than_the_column_are_refused():
    data = payload_data()
    data["accounts"][0]["transactions"][0]["notes"] = "x" * 1001
    with pytest.raises(PayloadError):
        parse_payload(data, ALL_KINDS)


async def _t1(session, bank_connection):
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    (row,) = await _rows(session, Transaction, account_id=account.id, external_id="t1")
    return row


def _with_text(**fields):
    data = payload_data()
    data["accounts"][0]["transactions"][0].update(fields)
    return data


@pytest.mark.asyncio
async def test_a_plugin_corrects_its_text_on_rows_nobody_edited(session: AsyncSession, bank_connection, test_user):
    """A plugin that learns to read its bank better sends better text for rows it sent before: they take it."""
    await _pipeline(session, bank_connection, _with_text(description="VISAEXAMPLE SHOP", payee="VISAEXAMPLE SHOP", notes="raw"), test_user)
    await _pipeline(session, bank_connection, _with_text(description="EXAMPLE SHOP", payee="EXAMPLE SHOP", notes="Ihr Einkauf"), test_user)
    row = await _t1(session, bank_connection)
    assert (row.description, row.payee, row.notes) == ("EXAMPLE SHOP", "EXAMPLE SHOP", "Ihr Einkauf")
    payee = await session.get(Payee, row.payee_id) if row.payee_id else None
    assert payee is not None and payee.name == "EXAMPLE SHOP"


@pytest.mark.asyncio
async def test_text_someone_edited_stays(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, _with_text(description="VISAEXAMPLE SHOP", payee="VISAEXAMPLE SHOP", notes="raw"), test_user)
    row = await _t1(session, bank_connection)
    row.description, row.payee, row.notes = "Groceries", "My shop", "split with Ann"
    await session.commit()
    await _pipeline(session, bank_connection, _with_text(description="EXAMPLE SHOP", payee="EXAMPLE SHOP", notes="Ihr Einkauf"), test_user)
    row = await _t1(session, bank_connection)
    assert (row.description, row.payee, row.notes) == ("Groceries", "My shop", "split with Ann")


@pytest.mark.asyncio
async def test_an_amount_the_column_rounds_still_takes_a_correction(session: AsyncSession, bank_connection, test_user):
    """The contract takes eight decimals, the row's column keeps two.

    The snapshot records the figure the column stores, so a later correction
    still applies; and a payload repeating the same rounded amount is not an
    edit, so it must not clear what the amount block clears (a typed
    conversion is the one that shows it). SQLite doesn't round the column, so
    the three decimals here are what make an unrounded comparison fail.
    """
    await _pipeline(session, bank_connection, _with_text(amount="12.345", type="credit"), test_user)
    row = await _t1(session, bank_connection)
    assert row.raw_data["securo_synced"]["amount"] == "12.35"

    row.amount_primary_typed = Decimal("99.99")
    await session.commit()
    await _pipeline(session, bank_connection, _with_text(amount="12.345", type="credit"), test_user)
    row = await _t1(session, bank_connection)
    assert row.amount_primary_typed == Decimal("99.99")

    await _pipeline(session, bank_connection, _with_text(amount="13.00", type="credit"), test_user)
    row = await _t1(session, bank_connection)
    assert (row.amount, row.raw_data["securo_synced"]["amount"]) == (Decimal("13.00"), "13.00")


@pytest.mark.asyncio
async def test_a_twin_under_a_new_id_corrects_the_notes_and_later_syncs_still_can(
    session: AsyncSession, bank_connection, test_user
):
    """A pending row that posts as a twin under a new id takes the twin's
    notes in place of its own, not beside them, so they stay the plugin's and
    a later correction reaches them too."""
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", status="pending", notes="raw")), test_user)
    await _pipeline(session, bank_connection, _account_txns(_txn("p2", notes="Ihr Einkauf")), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.external_id, row.notes) == ("p2", "Ihr Einkauf")

    await _pipeline(session, bank_connection, _account_txns(_txn("p2", notes="Ihr Einkauf, order 123")), test_user)
    row = await _only_row(session, bank_connection)
    assert row.notes == "Ihr Einkauf, order 123"


@pytest.mark.asyncio
async def test_a_twin_under_a_new_id_corrects_the_payee_and_later_syncs_still_can(
    session: AsyncSession, bank_connection, test_user
):
    """The payee follows a twin under a new id like the notes do, linked
    payee included, so the row doesn't read as edited from then on."""
    await _pipeline(session, bank_connection, _account_txns({**_txn("p1", status="pending"), "payee": "VISA SHOP"}), test_user)
    await _pipeline(session, bank_connection, _account_txns({**_txn("p2"), "payee": "Example Shop"}), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.external_id, row.payee) == ("p2", "Example Shop")
    payee = await session.get(Payee, row.payee_id) if row.payee_id else None
    assert payee is not None and payee.name == "Example Shop"

    await _pipeline(session, bank_connection, _account_txns({**_txn("p2"), "payee": "Example Shop GmbH"}), test_user)
    row = await _only_row(session, bank_connection)
    payee = await session.get(Payee, row.payee_id) if row.payee_id else None
    assert row.payee == "Example Shop GmbH" and payee is not None and payee.name == "Example Shop GmbH"


@pytest.mark.asyncio
async def test_a_stale_pending_twin_leaves_the_posted_row_as_it_is(
    session: AsyncSession, bank_connection, test_user
):
    """A pending entry that comes back under its old id after the row posted
    changes neither its payee nor its notes, nor the snapshot later
    corrections compare against."""
    pending = {**_txn("p1", status="pending", notes="raw"), "payee": "VISA SHOP"}
    posted = {**_txn("p2", notes="Ihr Einkauf"), "payee": "Example Shop"}
    await _pipeline(session, bank_connection, _account_txns(pending), test_user)
    await _pipeline(session, bank_connection, _account_txns(posted), test_user)
    await _pipeline(session, bank_connection, _account_txns(pending), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.external_id, row.status, row.payee, row.notes) == ("p2", "posted", "Example Shop", "Ihr Einkauf")
    sent = (row.raw_data or {})["securo_synced"]
    assert (sent["payee"], sent["notes"]) == ("Example Shop", "Ihr Einkauf")


@pytest.mark.asyncio
async def test_a_payee_a_stale_pending_twin_fills_in_still_takes_a_correction(
    session: AsyncSession, bank_connection, test_user
):
    """A posted row with no payee takes one from a stale pending twin; the
    snapshot records it, so the posted entry's later correction applies."""
    await _pipeline(session, bank_connection, _account_txns(_txn("p1", status="pending")), test_user)
    await _pipeline(session, bank_connection, _account_txns(_txn("p2")), test_user)
    await _pipeline(session, bank_connection, _account_txns({**_txn("p1", status="pending"), "payee": "VISA SHOP"}), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.status, row.payee) == ("posted", "VISA SHOP")
    external_id = row.external_id
    assert external_id is not None

    corrected = {**_txn(external_id), "payee": "Example Shop"}
    await _pipeline(session, bank_connection, _account_txns(corrected), test_user)
    row = await _only_row(session, bank_connection)
    assert row.payee == "Example Shop"


@pytest.mark.asyncio
async def test_a_hidden_row_takes_no_correction_from_its_twin(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, _account_txns({**_txn("p1", status="pending"), "payee": "VISA SHOP"}), test_user)
    row = await _only_row(session, bank_connection)
    row.is_ignored = True
    await session.commit()
    await _pipeline(session, bank_connection, _account_txns({**_txn("p2", notes="Ihr Einkauf"), "payee": "Example Shop"}), test_user)
    row = await _only_row(session, bank_connection)
    assert (row.payee, row.notes) == ("VISA SHOP", None)


@pytest.mark.asyncio
async def test_a_payee_of_only_spaces_is_none(session: AsyncSession, bank_connection, test_user):
    """Securo can't name a payee entity after blank text: a blank payee is none, sent first or as a correction."""
    await _pipeline(session, bank_connection, _with_text(payee="   "), test_user)
    row = await _t1(session, bank_connection)
    assert (row.payee, row.payee_id) == (None, None)

    await _pipeline(session, bank_connection, _with_text(payee="Example Shop"), test_user)
    await _pipeline(session, bank_connection, _with_text(payee="  "), test_user)
    row = await _t1(session, bank_connection)
    assert (row.payee, row.payee_id) == (None, None)


@pytest.mark.asyncio
async def test_notes_someone_cleared_stay_cleared_after_the_plugin_sends_none(
    session: AsyncSession, bank_connection, test_user
):
    """A payload without notes must not make the cleared row look like the plugin's again."""
    await _pipeline(session, bank_connection, _with_text(notes="raw"), test_user)
    row = await _t1(session, bank_connection)
    row.notes = None
    await session.commit()
    await _pipeline(session, bank_connection, _with_text(notes=None), test_user)
    await _pipeline(session, bank_connection, _with_text(notes="corrected"), test_user)
    row = await _t1(session, bank_connection)
    assert row.notes is None


@pytest.mark.asyncio
async def test_notes_someone_cleared_stay_cleared(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, _with_text(notes="raw"), test_user)
    row = await _t1(session, bank_connection)
    row.notes = None
    await session.commit()
    await _pipeline(session, bank_connection, _with_text(notes="raw"), test_user)
    await _pipeline(session, bank_connection, _with_text(notes="Ihr Einkauf"), test_user)
    row = await _t1(session, bank_connection)
    assert row.notes is None


@pytest.mark.asyncio
async def test_a_row_from_before_the_snapshot_gets_its_empty_notes_filled(session: AsyncSession, bank_connection, test_user):
    """Rows a plugin sent before Securo kept what it sent: their empty notes are filled, nothing else is guessed."""
    await _pipeline(session, bank_connection, _with_text(description="VISAEXAMPLE SHOP", payee="VISAEXAMPLE SHOP"), test_user)
    row = await _t1(session, bank_connection)
    row.raw_data = None
    await session.commit()
    await _pipeline(session, bank_connection, _with_text(description="EXAMPLE SHOP", payee="EXAMPLE SHOP", notes="Ihr Einkauf"), test_user)
    row = await _t1(session, bank_connection)
    assert (row.description, row.payee, row.notes) == ("EXAMPLE SHOP", "VISAEXAMPLE SHOP", "Ihr Einkauf")


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
        assert provider is not None
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


@pytest.mark.asyncio
async def test_a_stale_pending_entry_does_not_duplicate_its_posted_row(session: AsyncSession, bank_connection, test_user):
    """A pending entry that comes back after it posted under a new id is a
    stale twin of the posted row: the row keeps the posted entry's id, so the
    posted entry still finds it and nothing is inserted twice."""
    pending = {"external_id": "p1", "description": "Example Shop", "amount": "12.34", "date": "2026-11-14",
               "type": "debit", "status": "pending"}
    posted = {**pending, "external_id": "p2", "status": "posted"}
    for transactions in ([pending], [posted], [pending], [posted], [pending, posted]):
        await _pipeline(session, bank_connection, payload_data(transactions=transactions, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    rows = await _rows(session, Transaction, account_id=account.id)
    assert [(row.external_id, row.status) for row in rows] == [("p2", "posted")]


@pytest.mark.asyncio
async def test_replaces_external_id_settles_pending_even_when_amount_and_date_change(
    session: AsyncSession, bank_connection, test_user
):
    """A posted entry naming `replaces_external_id` upgrades that exact pending
    row in place even when tip/hold amount and posting date shifted, preserving
    user-assigned category and notes (ADR 0058)."""
    from app.models.category import Category

    cat = Category(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        name="Dining",
        icon="utensils",
        color="#FF5500",
    )
    session.add(cat)
    await session.flush()
    cat_id = cat.id

    pending = {
        "external_id": "p-hold-1",
        "description": "Corner Bistro",
        "payee": "Corner Bistro",
        "amount": "20.00",
        "date": "2026-11-14",
        "type": "debit",
        "status": "pending",
    }
    await _pipeline(session, bank_connection, payload_data(transactions=[pending], holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    (stored,) = await _rows(session, Transaction, account_id=account_id)
    stored_id = stored.id
    stored.category_id = cat_id
    stored.notes = "Dinner with team"
    await session.commit()

    settled = {
        "external_id": "p-posted-1",
        "replaces_external_id": "p-hold-1",
        "description": "Corner Bistro",
        "payee": "Corner Bistro",
        "amount": "24.00",
        "date": "2026-11-16",
        "type": "debit",
        "status": "posted",
    }
    # Plaid also includes the old pending ID in `removed_transaction_ids` on the same delta.
    delta = payload_data(transactions=[settled], holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["p-hold-1"]
    await _pipeline(session, bank_connection, delta, test_user)

    rows = await _rows(session, Transaction, account_id=account_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == stored_id
    assert row.external_id == "p-posted-1"
    assert row.status == "posted"
    assert row.amount == Decimal("24.00")
    assert row.date == date(2026, 11, 16)
    assert row.category_id == cat_id
    assert row.notes == "Dinner with team"


@pytest.mark.asyncio
async def test_removed_transaction_ids_deletes_untouched_and_preserves_user_work(
    session: AsyncSession, bank_connection, test_user
):
    """Explicit removals delete untouched synced entries (cancelled holds and
    reversed entries) while preserving rows the user edited, flagged, or linked."""
    txns = [
        {"external_id": "hold-cancel", "description": "Gas Hold", "amount": "50.00", "date": "2026-11-14", "type": "debit", "status": "pending"},
        {"external_id": "hold-edited", "description": "Hotel Hold", "amount": "200.00", "date": "2026-11-14", "type": "debit", "status": "pending"},
        {"external_id": "posted-reversed", "description": "Duplicate Fee", "payee": "Bank", "amount": "5.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "posted-flagged", "description": "Flagged Fee", "amount": "9.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=txns, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    by_ext = {row.external_id: row for row in await _rows(session, Transaction, account_id=account_id)}
    by_ext["hold-edited"].notes = "User kept this note"
    by_ext["posted-flagged"].is_flagged = True
    await session.commit()

    delta = payload_data(transactions=[], holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = [
        "hold-cancel",
        "hold-edited",
        "posted-reversed",
        "posted-flagged",
    ]
    await _pipeline(session, bank_connection, delta, test_user)

    remaining = {row.external_id for row in await _rows(session, Transaction, account_id=account_id)}
    assert remaining == {"hold-edited", "posted-flagged"}


@pytest.mark.asyncio
async def test_incremental_delta_advances_last_sync_and_preserves_same_day_repeat_next_to_transfer(
    session: AsyncSession, bank_connection, test_user
):
    """An incremental cursor-based delta advances `last_sync_at` without letting
    phantom duplicate cleanup delete a legitimate repeat transaction whose paired
    sibling simply wasn't part of today's delta (ADR 0058)."""
    first = payload_data(
        transactions=[
            {"external_id": "xfer-1", "description": "Transfer Out", "amount": "100.00", "date": "2026-11-15", "type": "debit", "status": "posted"},
        ],
        holdings=None,
    )
    first["accounts"][0]["incremental"] = True
    await _pipeline(session, bank_connection, first, test_user)
    await session.refresh(bank_connection)
    assert bank_connection.last_sync_at is not None

    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    (first_tx,) = await _rows(session, Transaction, account_id=account_id)
    first_tx.transfer_pair_id = uuid.uuid4()
    await session.commit()

    second = payload_data(
        transactions=[
            {"external_id": "xfer-2", "description": "Transfer Out", "amount": "100.00", "date": "2026-11-15", "type": "debit", "status": "posted"},
        ],
        holdings=None,
    )
    second["accounts"][0]["incremental"] = True
    await _pipeline(session, bank_connection, second, test_user)

    ext_ids = {row.external_id for row in await _rows(session, Transaction, account_id=account_id)}
    assert ext_ids == {"xfer-1", "xfer-2"}


@pytest.mark.asyncio
async def test_synced_loan_account_persists_terms_and_preserves_them_when_omitted(
    session: AsyncSession, bank_connection, test_user
):
    """Loan contract terms flow from the provider contract onto `Account` and
    survive a later sync that omits them (ADR 0058)."""
    first = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "nelnet-1",
                "name": "Nelnet Group AA",
                "type": "loan",
                "balance": "14200.50",
                "currency": "USD",
                "payment_due_day": 21,
                "minimum_payment": "185.00",
                "loan_kind": "student",
                "loan_original_amount": "20000.00",
                "loan_interest_rate": "4.99",
                "loan_rate_period": "yearly_nominal",
                "loan_term_months": 120,
                "loan_first_payment_date": "2021-06-01",
                "loan_amortization": "price",
                "incremental": True,
                "transactions": [],
            }
        ],
    }
    await _pipeline(session, bank_connection, first, test_user)
    (loan,) = await _rows(session, Account, connection_id=bank_connection.id)
    assert loan.type == "loan"
    assert loan.balance == Decimal("14200.50")
    assert loan.loan_kind == "student"
    assert loan.loan_original_amount == Decimal("20000.00")
    assert loan.loan_interest_rate == Decimal("4.99")
    assert loan.loan_rate_period == "yearly_nominal"
    assert loan.loan_term_months == 120
    assert loan.loan_first_payment_date == date(2021, 6, 1)
    assert loan.loan_amortization == "price"
    assert loan.payment_due_day == 21
    assert loan.minimum_payment == Decimal("185.00")

    # Follow-up sync omits optional terms: stored values are preserved.
    followup = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "nelnet-1",
                "name": "Nelnet Group AA",
                "type": "loan",
                "balance": "14015.50",
                "currency": "USD",
                "incremental": True,
                "transactions": [],
            }
        ],
    }
    await _pipeline(session, bank_connection, followup, test_user)
    await session.refresh(loan)
    assert loan.balance == Decimal("14015.50")
    assert loan.loan_kind == "student"
    assert loan.loan_original_amount == Decimal("20000.00")
    assert loan.loan_interest_rate == Decimal("4.99")
    assert loan.payment_due_day == 21
    assert loan.minimum_payment == Decimal("185.00")


@pytest.mark.asyncio
async def test_removed_transaction_ids_protects_payee_id_and_typed_fx_edits_on_posted_rows(
    session: AsyncSession, bank_connection, test_user
):
    """Reassigning or clearing `payee_id`, setting `payee_id` on a payeeless row,
    or setting `amount_primary_typed` on a posted synced row must protect it from
    `removed_transaction_ids`, while untouched auto-matched payee rows still delete."""
    from app.models.payee import Payee
    from app.schemas.transaction import TransactionUpdate
    from app.services import transaction_service

    custom_payee = Payee(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        name="User Reassigned Payee",
        source="manual",
    )
    session.add(custom_payee)
    await session.flush()
    custom_payee_id = custom_payee.id

    txns = [
        {"external_id": "untouched-with-payee", "description": "Fee", "payee": "Bank", "amount": "5.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "reassigned-payee", "description": "Coffee", "payee": "Bank", "amount": "6.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "cleared-payee", "description": "Snack", "payee": "Bank", "amount": "7.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "added-payee", "description": "Transfer", "amount": "8.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "typed-fx", "description": "Euro Shop", "amount": "10.00", "currency": "EUR", "date": "2026-11-14", "type": "debit", "status": "posted"},
        {"external_id": "reverted-payee-patch", "description": "Patched", "payee": "Bank", "amount": "9.00", "date": "2026-11-14", "type": "debit", "status": "posted"},
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=txns, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    by_ext = {row.external_id: row for row in await _rows(session, Transaction, account_id=account_id)}

    original_bank_payee_id = by_ext["reverted-payee-patch"].payee_id
    assert original_bank_payee_id is not None

    by_ext["reassigned-payee"].payee_id = custom_payee_id
    by_ext["cleared-payee"].payee_id = None
    by_ext["added-payee"].payee_id = custom_payee_id
    by_ext["typed-fx"].amount_primary_typed = Decimal("11.25")
    by_ext["typed-fx"].amount_primary_typed_currency = "USD"
    await session.commit()

    # Edit via update_transaction and revert back to verify USER_EDITED_KEY holds on posted rows too.
    await transaction_service.update_transaction(
        session,
        by_ext["reverted-payee-patch"].id,
        bank_connection.workspace_id,
        test_user.id,
        TransactionUpdate(payee_id=custom_payee_id),
    )
    await transaction_service.update_transaction(
        session,
        by_ext["reverted-payee-patch"].id,
        bank_connection.workspace_id,
        test_user.id,
        TransactionUpdate(payee_id=original_bank_payee_id),
    )

    delta = payload_data(transactions=[], holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = list(txns_id["external_id"] for txns_id in txns)
    await _pipeline(session, bank_connection, delta, test_user)

    remaining = {row.external_id for row in await _rows(session, Transaction, account_id=account_id)}
    assert remaining == {
        "reassigned-payee",
        "cleared-payee",
        "added-payee",
        "typed-fx",
        "reverted-payee-patch",
    }


@pytest.mark.asyncio
async def test_replaces_external_id_restamps_fx_to_settled_date(
    session: AsyncSession, bank_connection, test_user
):
    """When a foreign-currency pending transaction settles on a new date via
    `replaces_external_id` (both with and without an amount change), FX stamps
    use the settled date's rate rather than the old pending date's rate."""
    from app.models.fx_rate import FxRate

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add_all(
        [
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 14), rate=Decimal("0.8"), source="test"),
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 16), rate=Decimal("0.5"), source="test"),
        ]
    )
    await session.commit()

    pending = [
        {
            "external_id": "fx-hold-same-amt",
            "description": "Hotel Auth",
            "amount": "100.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        },
        {
            "external_id": "fx-hold-tip",
            "description": "Dinner Auth",
            "amount": "40.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        },
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=pending, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    before = {r.external_id: r for r in await _rows(session, Transaction, account_id=account_id)}
    same_amt_id = before["fx-hold-same-amt"].id
    tip_id = before["fx-hold-tip"].id
    # On 2026-11-14 (1 USD = 0.8 EUR -> 1 EUR = 1.25 USD): 100 EUR -> 125.00 USD, 40 EUR -> 50.00 USD.
    assert before["fx-hold-same-amt"].amount_primary == Decimal("125.00")
    assert before["fx-hold-tip"].amount_primary == Decimal("50.00")

    settled = [
        {
            "external_id": "fx-posted-same-amt",
            "replaces_external_id": "fx-hold-same-amt",
            "description": "Hotel Settled",
            "amount": "100.00",
            "currency": "EUR",
            "date": "2026-11-16",
            "type": "debit",
            "status": "posted",
        },
        {
            "external_id": "fx-posted-tip",
            "replaces_external_id": "fx-hold-tip",
            "description": "Dinner Settled",
            "amount": "50.00",
            "currency": "EUR",
            "date": "2026-11-16",
            "type": "debit",
            "status": "posted",
        },
    ]
    delta = payload_data(transactions=settled, holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["fx-hold-same-amt", "fx-hold-tip"]
    await _pipeline(session, bank_connection, delta, test_user)

    after = {r.external_id: r for r in await _rows(session, Transaction, account_id=account_id)}
    assert set(after) == {"fx-posted-same-amt", "fx-posted-tip"}
    assert after["fx-posted-same-amt"].id == same_amt_id
    assert after["fx-posted-tip"].id == tip_id
    # On 2026-11-16 (1 USD = 0.5 EUR -> 1 EUR = 2.00 USD): 100 EUR -> 200.00 USD, 50 EUR -> 100.00 USD.
    assert after["fx-posted-same-amt"].amount_primary == Decimal("200.00")
    assert after["fx-posted-same-amt"].amount_account == Decimal("200.00")
    assert after["fx-posted-tip"].amount_primary == Decimal("100.00")
    assert after["fx-posted-tip"].amount_account == Decimal("100.00")


@pytest.mark.asyncio
async def test_replaces_external_id_preserves_user_account_amount_on_date_shift_with_bank_amount(
    session: AsyncSession, bank_connection, test_user
):
    """When a foreign-currency pending row has a user-typed primary/account
    conversion (`amount_account_source == 'user'`) and settles on a shifted date
    with unchanged amount and a provider-supplied `amount_in_account_currency`,
    settlement must preserve the user's `amount_account` and `amount_account_source`."""
    from app.models.fx_rate import FxRate
    from app.schemas.transaction import TransactionUpdate
    from app.services import transaction_service

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add_all(
        [
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 14), rate=Decimal("0.8"), source="test"),
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 16), rate=Decimal("0.5"), source="test"),
        ]
    )
    await session.commit()

    pending = [
        {
            "external_id": "fx-user-hold",
            "description": "Euro Shop",
            "amount": "100.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        }
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=pending, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    (stored,) = await _rows(session, Transaction, account_id=account_id)
    stored_id = stored.id

    await transaction_service.update_transaction(
        session,
        stored_id,
        bank_connection.workspace_id,
        test_user.id,
        TransactionUpdate(amount_primary=Decimal("130.00")),
    )
    await session.refresh(stored)
    assert stored.amount_primary_typed == Decimal("130.00")
    assert stored.amount_account == Decimal("130.00")
    assert stored.amount_account_source == "user"

    settled = [
        {
            "external_id": "fx-user-posted",
            "replaces_external_id": "fx-user-hold",
            "description": "Euro Shop",
            "amount": "100.00",
            "currency": "EUR",
            "amount_in_account_currency": "200.00",
            "date": "2026-11-16",
            "type": "debit",
            "status": "posted",
        }
    ]
    delta = payload_data(transactions=settled, holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["fx-user-hold"]
    await _pipeline(session, bank_connection, delta, test_user)

    (after,) = await _rows(session, Transaction, account_id=account_id)
    assert after.id == stored_id
    assert after.status == "posted"
    assert after.date == date(2026, 11, 16)
    assert after.amount_primary == Decimal("130.00")
    assert after.amount_primary_typed == Decimal("130.00")
    assert after.amount_account == Decimal("130.00")
    assert after.amount_account_source == "user"


@pytest.mark.asyncio
async def test_replaces_external_id_updates_currency_and_restamps_fx_on_same_date_and_amount(
    session: AsyncSession, bank_connection, test_user
):
    """When a pending hold settles under a different currency with the same date
    and numeric amount, settlement must adopt the posted currency, drop any stale
    conversion typed for the old currency, and restamp primary/account FX."""
    from app.models.fx_rate import FxRate
    from app.schemas.transaction import TransactionUpdate
    from app.services import transaction_service

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add_all(
        [
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 14), rate=Decimal("0.8"), source="test"),
            FxRate(base_currency="USD", quote_currency="GBP", date=date(2026, 11, 14), rate=Decimal("0.5"), source="test"),
        ]
    )
    await session.commit()

    pending = [
        {
            "external_id": "fx-ccy-hold",
            "description": "Cross-Border",
            "amount": "100.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        }
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=pending, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    (stored,) = await _rows(session, Transaction, account_id=account_id)
    stored_id = stored.id

    await transaction_service.update_transaction(
        session,
        stored_id,
        bank_connection.workspace_id,
        test_user.id,
        TransactionUpdate(amount_primary=Decimal("130.00")),
    )

    settled = [
        {
            "external_id": "fx-ccy-posted",
            "replaces_external_id": "fx-ccy-hold",
            "description": "Cross-Border",
            "amount": "100.00",
            "currency": "GBP",
            "date": "2026-11-14",
            "type": "debit",
            "status": "posted",
        }
    ]
    delta = payload_data(transactions=settled, holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["fx-ccy-hold"]
    await _pipeline(session, bank_connection, delta, test_user)

    (after,) = await _rows(session, Transaction, account_id=account_id)
    assert after.id == stored_id
    assert after.external_id == "fx-ccy-posted"
    assert after.status == "posted"
    assert after.currency == "GBP"
    assert after.amount_primary_typed is None
    assert after.amount_primary == Decimal("200.00")
    assert after.fx_rate_used == Decimal("2")
    assert after.amount_account == Decimal("200.00")


@pytest.mark.asyncio
async def test_replaces_external_id_currency_change_clears_stale_fx_when_rate_missing(
    session: AsyncSession, bank_connection, test_user
):
    """When a pending EUR hold settles as GBP and no GBP FX rate exists,
    settlement must clear stale EUR primary/rate/account stamps (both when the
    numeric amount stays the same and when it changes)."""
    from app.models.fx_rate import FxRate
    from app.schemas.transaction import TransactionUpdate
    from app.services import transaction_service

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="EUR",
            date=date(2026, 11, 14),
            rate=Decimal("0.8"),
            source="test",
        )
    )
    await session.commit()

    pending = [
        {
            "external_id": "fx-norate-same-hold",
            "description": "London Hotel",
            "amount": "100.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        },
        {
            "external_id": "fx-norate-diff-hold",
            "description": "London Dinner",
            "amount": "40.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        },
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=pending, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    before = {r.external_id: r for r in await _rows(session, Transaction, account_id=account_id)}
    same_id = before["fx-norate-same-hold"].id
    diff_id = before["fx-norate-diff-hold"].id

    # Give the same-amount hold a user-typed EUR->USD override; leave the
    # tip-changed hold with its derived EUR->USD rate stamp.
    await transaction_service.update_transaction(
        session,
        same_id,
        bank_connection.workspace_id,
        test_user.id,
        TransactionUpdate(amount_primary=Decimal("130.00")),
    )
    assert before["fx-norate-diff-hold"].amount_primary == Decimal("50.00")
    assert before["fx-norate-diff-hold"].fx_rate_used == Decimal("1.25")
    assert before["fx-norate-diff-hold"].amount_account == Decimal("50.00")

    settled = [
        {
            "external_id": "fx-norate-same-posted",
            "replaces_external_id": "fx-norate-same-hold",
            "description": "London Hotel",
            "amount": "100.00",
            "currency": "GBP",
            "date": "2026-11-14",
            "type": "debit",
            "status": "posted",
        },
        {
            "external_id": "fx-norate-diff-posted",
            "replaces_external_id": "fx-norate-diff-hold",
            "description": "London Dinner",
            "amount": "55.00",
            "currency": "GBP",
            "date": "2026-11-15",
            "type": "debit",
            "status": "posted",
        },
    ]
    delta = payload_data(transactions=settled, holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = [
        "fx-norate-same-hold",
        "fx-norate-diff-hold",
    ]
    await _pipeline(session, bank_connection, delta, test_user)

    after = {r.external_id: r for r in await _rows(session, Transaction, account_id=account_id)}
    assert set(after) == {"fx-norate-same-posted", "fx-norate-diff-posted"}
    same_row = after["fx-norate-same-posted"]
    diff_row = after["fx-norate-diff-posted"]
    assert same_row.id == same_id
    assert diff_row.id == diff_id

    for row, expected_amount in (
        (same_row, Decimal("100.00")),
        (diff_row, Decimal("55.00")),
    ):
        assert row.status == "posted"
        assert row.currency == "GBP"
        assert row.amount == expected_amount
        assert row.amount_primary is None
        assert row.fx_rate_used is None
        assert row.amount_primary_typed is None
        assert row.amount_primary_typed_currency is None
        assert row.amount_account is None
        assert row.amount_account_source is None


@pytest.mark.asyncio
async def test_replaces_external_id_currency_and_amount_change_restamps_when_rate_present(
    session: AsyncSession, bank_connection, test_user
):
    """When a pending EUR hold settles in GBP with a changed amount and a GBP
    rate is present, settlement clears the old EUR stamp and restamps cleanly in GBP."""
    from app.models.fx_rate import FxRate

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add_all(
        [
            FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 11, 14), rate=Decimal("0.8"), source="test"),
            FxRate(base_currency="USD", quote_currency="GBP", date=date(2026, 11, 15), rate=Decimal("0.5"), source="test"),
        ]
    )
    await session.commit()

    pending = [
        {
            "external_id": "fx-ccy-amt-hold",
            "description": "Cross-Border Tip",
            "amount": "40.00",
            "currency": "EUR",
            "date": "2026-11-14",
            "type": "debit",
            "status": "pending",
        }
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=pending, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    (before,) = await _rows(session, Transaction, account_id=account_id)
    before_id = before.id
    assert before.amount_primary == Decimal("50.00")
    assert before.fx_rate_used == Decimal("1.25")

    settled = [
        {
            "external_id": "fx-ccy-amt-posted",
            "replaces_external_id": "fx-ccy-amt-hold",
            "description": "Cross-Border Tip",
            "amount": "60.00",
            "currency": "GBP",
            "date": "2026-11-15",
            "type": "debit",
            "status": "posted",
        }
    ]
    delta = payload_data(transactions=settled, holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["fx-ccy-amt-hold"]
    await _pipeline(session, bank_connection, delta, test_user)

    (after,) = await _rows(session, Transaction, account_id=account_id)
    assert after.id == before_id
    assert after.external_id == "fx-ccy-amt-posted"
    assert after.status == "posted"
    assert after.currency == "GBP"
    assert after.amount == Decimal("60.00")
    assert after.amount_primary == Decimal("120.00")
    assert after.fx_rate_used == Decimal("2")
    assert after.amount_account == Decimal("120.00")
    assert after.amount_primary_typed is None
    assert after.amount_account_source is None


@pytest.mark.asyncio
async def test_removed_transaction_ids_preserves_mortgage_breakdown_and_bulk_cleared_posted_category(
    session: AsyncSession, bank_connection, test_user
):
    """Explicit removals must preserve posted synced payments linked to a
    MortgagePaymentAllocation and posted synced rows whose category was cleared
    through bulk_update_category."""
    from app.models.category import Category
    from app.models.mortgage_payment_allocation import MORTGAGE_PRINCIPAL_SOURCE, MortgagePaymentAllocation
    from app.services import transaction_service

    cat = Category(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        name="Bills",
        icon="home",
        color="#3366FF",
    )
    loan = Account(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        name="Home Mortgage",
        type="loan",
        balance=Decimal("-200000.00"),
        currency="USD",
    )
    session.add_all([cat, loan])
    await session.flush()
    cat_id = cat.id
    loan_id = loan.id

    txns = [
        {
            "external_id": "mort-pmt",
            "description": "Mortgage Payment",
            "amount": "1500.00",
            "date": "2026-11-14",
            "type": "debit",
            "status": "posted",
        },
        {
            "external_id": "bulk-cleared",
            "description": "Utility Charge",
            "amount": "75.00",
            "date": "2026-11-14",
            "type": "debit",
            "status": "posted",
        },
        {
            "external_id": "untouched-posted",
            "description": "Reversed Fee",
            "amount": "10.00",
            "date": "2026-11-14",
            "type": "debit",
            "status": "posted",
        },
    ]
    await _pipeline(session, bank_connection, payload_data(transactions=txns, holdings=None), test_user)
    (account,) = await _rows(session, Account, connection_id=bank_connection.id)
    account_id = account.id
    by_ext = {r.external_id: r for r in await _rows(session, Transaction, account_id=account_id)}
    mort_pmt_id = by_ext["mort-pmt"].id
    bulk_cleared_id = by_ext["bulk-cleared"].id

    # Attach a MortgagePaymentAllocation to mort-pmt.
    principal_tx = Transaction(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        account_id=loan_id,
        date=date(2026, 11, 14),
        description="Mortgage principal",
        amount=Decimal("600.00"),
        type="credit",
        currency="USD",
        source=MORTGAGE_PRINCIPAL_SOURCE,
    )
    session.add(principal_tx)
    await session.flush()
    principal_tx_id = principal_tx.id
    alloc = MortgagePaymentAllocation(
        workspace_id=bank_connection.workspace_id,
        payment_transaction_id=mort_pmt_id,
        loan_account_id=loan_id,
        loan_transaction_id=principal_tx_id,
        principal_amount=Decimal("600.00"),
        interest_amount=Decimal("900.00"),
    )
    session.add(alloc)
    # Give bulk-cleared an initial category, then clear it via bulk_update_category.
    by_ext["bulk-cleared"].category_id = cat_id
    await session.commit()

    updated = await transaction_service.bulk_update_category(
        session, bank_connection.workspace_id, [bulk_cleared_id], category_id=None
    )
    assert updated == 1

    delta = payload_data(transactions=[], holdings=None)
    delta["accounts"][0]["incremental"] = True
    delta["accounts"][0]["removed_transaction_ids"] = ["mort-pmt", "bulk-cleared", "untouched-posted"]
    await _pipeline(session, bank_connection, delta, test_user)

    remaining = {r.external_id for r in await _rows(session, Transaction, account_id=account_id)}
    assert remaining == {"mort-pmt", "bulk-cleared"}
    assert len(await _rows(session, MortgagePaymentAllocation, payment_transaction_id=mort_pmt_id)) == 1
    assert await session.get(Transaction, principal_tx_id) is not None


@pytest.mark.asyncio
async def test_phantom_cleanup_skips_accounts_omitted_from_reply_without_rate_limiting(
    session: AsyncSession, bank_connection, test_user
):
    """When a provider omits a previously synced account from its reply (even
    with no rate-limited accounts), phantom cleanup must skip that omitted
    account rather than deleting a legitimate same-day repeat transfer."""
    first = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "acc-a",
                "name": "Checking A",
                "type": "checking",
                "balance": "100.00",
                "currency": "USD",
                "transactions": [],
            },
            {
                "external_id": "acc-b",
                "name": "Checking B",
                "type": "checking",
                "balance": "200.00",
                "currency": "USD",
                "transactions": [
                    {
                        "external_id": "b-xfer-1",
                        "description": "Transfer Out",
                        "amount": "50.00",
                        "date": "2026-11-15",
                        "type": "debit",
                        "status": "posted",
                    },
                    {
                        "external_id": "b-xfer-2",
                        "description": "Transfer Out",
                        "amount": "50.00",
                        "date": "2026-11-15",
                        "type": "debit",
                        "status": "posted",
                    },
                ],
            },
        ],
    }
    await _pipeline(session, bank_connection, first, test_user)
    accounts = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_connection.id)}
    acc_b_id = accounts["acc-b"].id
    b_txns = {t.external_id: t for t in await _rows(session, Transaction, account_id=acc_b_id)}
    b_txns["b-xfer-1"].transfer_pair_id = uuid.uuid4()
    await session.commit()

    # Second sync returns only acc-a (acc-b is omitted, with zero rate-limited accounts).
    second = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "acc-a",
                "name": "Checking A",
                "type": "checking",
                "balance": "100.00",
                "currency": "USD",
                "transactions": [],
            }
        ],
    }
    await _pipeline(session, bank_connection, second, test_user)

    remaining_b = {t.external_id for t in await _rows(session, Transaction, account_id=acc_b_id)}
    assert remaining_b == {"b-xfer-1", "b-xfer-2"}


@pytest.mark.asyncio
async def test_omitted_holdings_deducts_retained_investment_holdings_and_handles_edge_cases(
    session: AsyncSession, bank_connection, test_user
):
    """When a follow-up sync omits `holdings` (`holdings=None`), an investment
    account that reports its gross balance as `auto_invested_balance` (e.g. Plaid)
    deducts the latest stored values of its live same-currency attributed holdings
    (capped at the gross balance), while a cash-only investment account (e.g. IBKR,
    no `auto_invested_balance`) keeps its full cash balance untouched. Positions
    with no stored value, negative/archived/sold/foreign-currency positions, and
    authoritative empty `holdings=[]` snapshots are handled cleanly."""
    from sqlalchemy import delete

    from app.models.asset_group import AssetGroup
    from app.models.asset_value import AssetValue

    # 1. Initial complete sync:
    #    - inv-1 reports 2750 gross balance with 2500 same-currency holdings -> 250 cash
    #    - inv-cash reports 500 cash-only balance (no auto_invested_balance) with a 1000 holding -> 500 cash
    #    - chk-1 is a non-investment account with an attributed holding -> stays 1000
    first = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "inv-1",
                "name": "Brokerage",
                "type": "investment",
                "balance": "2750.00",
                "currency": "USD",
                "auto_invested_balance": "2500.00",
                "incremental": True,
                "transactions": [],
            },
            {
                "external_id": "inv-cash",
                "name": "Broker Cash (USD)",
                "type": "investment",
                "balance": "500.00",
                "currency": "USD",
                "incremental": True,
                "transactions": [],
            },
            {
                "external_id": "chk-1",
                "name": "Checking",
                "type": "checking",
                "balance": "1000.00",
                "currency": "USD",
                "incremental": True,
                "transactions": [],
            },
        ],
        "holdings": [
            {
                "external_id": "h-vti",
                "name": "VTI",
                "type": "etf",
                "currency": "USD",
                "current_value": "2000.00",
                "account_external_id": "inv-1",
            },
            {
                "external_id": "h-vxus",
                "name": "VXUS",
                "type": "etf",
                "currency": "USD",
                "current_value": "500.00",
                "account_external_id": "inv-1",
            },
            {
                "external_id": "h-eur",
                "name": "Euro ETF",
                "type": "etf",
                "currency": "EUR",
                "current_value": "300.00",
                "account_external_id": "inv-1",
            },
            {
                "external_id": "h-ibkr-etf",
                "name": "IBKR ETF",
                "type": "etf",
                "currency": "USD",
                "current_value": "1000.00",
                "account_external_id": "inv-cash",
            },
            {
                "external_id": "h-chk-fund",
                "name": "Checking Fund",
                "type": "fund",
                "currency": "USD",
                "current_value": "400.00",
                "account_external_id": "chk-1",
            },
        ],
    }
    await _pipeline(session, bank_connection, first, test_user)
    accounts = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_connection.id)}
    assert accounts["inv-1"].balance == Decimal("250.00")
    assert accounts["inv-cash"].balance == Decimal("500.00")
    assert accounts["chk-1"].balance == Decimal("1000.00")

    assets = {a.external_id: a for a in await _rows(session, Asset, connection_id=bank_connection.id)}
    inv_wallet = await session.get(AssetGroup, assets["h-vti"].group_id)
    assert inv_wallet is not None

    # Update h-vti to have an older snapshot (1800) and newer snapshot (2500),
    # strip all AssetValue rows from h-vxus (simulating missing valuation), and
    # add negative, archived, and sold synced positions in inv-1's wallet.
    await session.execute(delete(AssetValue).where(AssetValue.asset_id.in_([assets["h-vti"].id, assets["h-vxus"].id])))
    session.add_all(
        [
            AssetValue(
                asset_id=assets["h-vti"].id,
                workspace_id=bank_connection.workspace_id,
                amount=Decimal("1800.00"),
                date=date(2026, 11, 1),
                source="sync",
            ),
            AssetValue(
                asset_id=assets["h-vti"].id,
                workspace_id=bank_connection.workspace_id,
                amount=Decimal("2500.00"),
                date=date(2026, 11, 15),
                source="sync",
            ),
        ]
    )
    short_asset = Asset(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        connection_id=bank_connection.id,
        source=bank_connection.provider,
        external_id="h-short",
        name="Short Put",
        type="investment",
        currency="USD",
        valuation_method="manual",
        group_id=inv_wallet.id,
    )
    archived_asset = Asset(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        connection_id=bank_connection.id,
        source=bank_connection.provider,
        external_id="h-archived",
        name="Old Stock",
        type="investment",
        currency="USD",
        valuation_method="manual",
        group_id=inv_wallet.id,
        is_archived=True,
    )
    sold_asset = Asset(
        user_id=test_user.id,
        workspace_id=bank_connection.workspace_id,
        connection_id=bank_connection.id,
        source=bank_connection.provider,
        external_id="h-sold",
        name="Sold Stock",
        type="investment",
        currency="USD",
        valuation_method="manual",
        group_id=inv_wallet.id,
        sell_date=date(2026, 11, 10),
    )
    session.add_all([short_asset, archived_asset, sold_asset])
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=short_asset.id,
                workspace_id=bank_connection.workspace_id,
                amount=Decimal("-150.00"),
                date=date(2026, 11, 15),
                source="sync",
            ),
            AssetValue(
                asset_id=archived_asset.id,
                workspace_id=bank_connection.workspace_id,
                amount=Decimal("900.00"),
                date=date(2026, 11, 15),
                source="sync",
            ),
            AssetValue(
                asset_id=sold_asset.id,
                workspace_id=bank_connection.workspace_id,
                amount=Decimal("700.00"),
                date=date(2026, 11, 15),
                source="sync",
            ),
        ]
    )
    await session.commit()

    # 2. Second sync omits holdings (holdings=None):
    #    - inv-1 reports gross balance as auto_invested_balance cap -> deducts stored 2500 -> 250 cash
    #    - inv-cash omits auto_invested_balance (cash-only brokerage) -> keeps 500 cash (not -500!)
    omitted = {
        "contract": 1,
        "accounts": [
            {
                "external_id": "inv-1",
                "name": "Brokerage",
                "type": "investment",
                "balance": "2750.00",
                "currency": "USD",
                "auto_invested_balance": "2750.00",
                "incremental": True,
                "transactions": [],
            },
            {
                "external_id": "inv-cash",
                "name": "Broker Cash (USD)",
                "type": "investment",
                "balance": "500.00",
                "currency": "USD",
                "incremental": True,
                "transactions": [],
            },
            {
                "external_id": "chk-1",
                "name": "Checking",
                "type": "checking",
                "balance": "1000.00",
                "currency": "USD",
                "incremental": True,
                "transactions": [],
            },
        ],
    }
    await _pipeline(session, bank_connection, omitted, test_user)
    accounts_after_omitted = {
        a.external_id: a for a in await _rows(session, Account, connection_id=bank_connection.id)
    }
    # Only h-vti's latest positive USD value (2500) is deducted -> 2750 - 2500 = 250.
    assert accounts_after_omitted["inv-1"].balance == Decimal("250.00")
    assert accounts_after_omitted["inv-cash"].balance == Decimal("500.00")
    assert accounts_after_omitted["chk-1"].balance == Decimal("1000.00")
    assets_after_omitted = {
        a.external_id: a for a in await _rows(session, Asset, connection_id=bank_connection.id)
    }
    assert assets_after_omitted["h-vti"].is_archived is False
    assert assets_after_omitted["h-vxus"].is_archived is False
    assert assets_after_omitted["h-ibkr-etf"].is_archived is False

    # 3. Third sync reports an authoritative empty portfolio (`holdings: []`, no auto_invested_balance):
    #    live holdings archive and the investment account takes the full 2750 cash balance.
    empty = {
        "contract": 1,
        "accounts": [
            {k: v for k, v in acc.items() if k != "auto_invested_balance"}
            for acc in omitted["accounts"]
        ],
        "holdings": [],
    }
    await _pipeline(session, bank_connection, empty, test_user)
    accounts_after_empty = {
        a.external_id: a for a in await _rows(session, Account, connection_id=bank_connection.id)
    }
    assert accounts_after_empty["inv-1"].balance == Decimal("2750.00")
    assert accounts_after_empty["inv-cash"].balance == Decimal("500.00")
    assets_after_empty = {
        a.external_id: a for a in await _rows(session, Asset, connection_id=bank_connection.id)
    }
    assert assets_after_empty["h-vti"].is_archived is True
    assert assets_after_empty["h-vxus"].is_archived is True
