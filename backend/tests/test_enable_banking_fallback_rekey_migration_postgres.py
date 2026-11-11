"""Migration 109 on real PostgreSQL: which fallback-keyed rows it re-keys."""
import importlib.util
import uuid
from typing import Any, cast
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.enable_banking import EnableBankingProvider, _txn_fingerprint

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "109_rekey_enable_banking_fallback_ids.py"
)
_SPEC = importlib.util.spec_from_file_location("eb_fallback_rekey_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)

ACCOUNT_UID = "acc-1"


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


def _payload(
    entry_ref="", amount="12.00", *, booking="2026-08-31", value="2026-08-31",
    transaction_date="2026-08-30", creditor="Shop A", creditor_iban=None,
) -> dict:
    raw = {
        "entry_reference": entry_ref,
        "transaction_amount": {"currency": "EUR", "amount": amount},
        "credit_debit_indicator": "DBIT",
        "value_date": value,
        "transaction_date": transaction_date,
        "creditor": {"name": creditor},
        "remittance_information": ["CARD PURCHASE"],
    }
    if booking:
        raw["booking_date"] = booking
    if creditor_iban:
        raw["creditor_account"] = {"iban": creditor_iban}
    return raw


def _legacy_id(raw: dict) -> str:
    return migration._legacy_fingerprint(ACCOUNT_UID, raw)


def _new_id(raw: dict, status="posted") -> str:
    parsed = EnableBankingProvider()._build_transaction(ACCOUNT_UID, raw, status, "auto")
    assert parsed is not None
    return parsed.external_id


async def _account(session, user_id, provider: str) -> Account:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider=provider,
        external_id=f"ext-{uuid.uuid4().hex[:8]}", institution_name="Bank",
        credentials={}, status="active", created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    account = Account(
        id=uuid.uuid4(), user_id=user_id, connection_id=conn.id, name="Giro",
        type="checking", balance=Decimal("0"), currency="EUR", external_id=ACCOUNT_UID,
    )
    session.add(account)
    await session.flush()
    return account


def _row(account, user_id, external_id, raw, *, status="posted", source="sync") -> Transaction:
    amount = raw["transaction_amount"]["amount"]
    return Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        external_id=external_id, description="CARD PURCHASE", amount=Decimal(amount),
        date=date.fromisoformat(
            raw.get("booking_date") or raw.get("value_date") or raw["transaction_date"]
        ),
        type="debit", currency="EUR", source=source, status=status, raw_data=raw,
        created_at=datetime.now(timezone.utc),
    )


async def _upgrade(session, monkeypatch) -> None:
    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's raw SQL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))

    def run(sync_conn):
        monkeypatch.setattr(migration.op, "get_bind", lambda: sync_conn)
        migration.upgrade()

    await conn.run_sync(run)


async def _external_id(session, transaction_id) -> str:
    return await session.scalar(
        select(Transaction.external_id).where(Transaction.id == transaction_id)
    )


# Ids computed by running the real _txn_fingerprint of each release (git tags
# v0.16.2 and v0.16.3, backend/app/providers/enable_banking.py), account "acc-1".
GOLDEN = {
    "name_only": {
        "raw": {
            "entry_reference": "",
            "transaction_amount": {
                "currency": "EUR",
                "amount": "12.34"
            },
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-08-23",
            "value_date": "2026-08-20",
            "transaction_date": "2026-08-19",
            "creditor": {
                "name": "Coffee Shop"
            },
            "remittance_information": [
                "CARD PURCHASE"
            ]
        },
        "v0.16.2": "fcf192182d5d787f5c01bccd3a3ac065",
        "v0.16.3": "cbc2789d092e130a3ff95f74c6a43d43"
    },
    "ibans": {
        "raw": {
            "transaction_amount": {
                "currency": "EUR",
                "amount": "100.00"
            },
            "credit_debit_indicator": "CRDT",
            "booking_date": "2026-09-01",
            "value_date": "2026-09-01",
            "creditor_account": {
                "iban": "DE89370400440532013000"
            },
            "debtor_account": {
                "iban": " FR1420041010050500013M02606 "
            },
            "debtor": {
                "name": "Payer"
            },
            "remittance_information": "SALARY"
        },
        "v0.16.2": "7184ee6c2c550f2ad0af09324ec06eb1",
        "v0.16.3": "825294a856ed57100fa37dcc3a3ecc5b"
    },
    "pending_no_booking": {
        "raw": {
            "entry_reference": "",
            "transaction_amount": {
                "currency": "EUR",
                "amount": "30.00"
            },
            "credit_debit_indicator": "DBIT",
            "value_date": "2026-09-02",
            "creditor_name": "Bakery",
            "remittance_information": []
        },
        "v0.16.2": "302bd8fa13df79f85eb735971abd7075",
        "v0.16.3": "43e8abc0f5db07e839ea487b5bf15656"
    },
    "long_remittance_list": {
        "raw": {
            "transaction_amount": {
                "currency": "BRL",
                "amount": "7.5"
            },
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-01",
            "transaction_date": "2026-09-30",
            "creditor": {
                "name": "Shop"
            },
            "remittance_information": [
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                "C"
            ]
        },
        "v0.16.2": "ed048aa79a4e83083662e4febbddbce2",
        "v0.16.3": "4cf8c878ffaf5b526b9262fa65445b68"
    },
    "zero_ref": {
        "raw": {
            "entry_reference": "0",
            "transaction_amount": {
                "currency": "EUR",
                "amount": "5.00"
            },
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-08-23",
            "value_date": "2026-08-20",
            "transaction_date": "2026-08-20",
            "creditor": {
                "name": "Coffee Shop"
            }
        },
        "v0.16.2": "4016db750882f036973b63da8747dc6c",
        "v0.16.3": "509fbc93465db1c716afcf95d502e0e5"
    }
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
async def test_frozen_recipes_match_the_released_ones(name):
    case = GOLDEN[name]
    assert migration._legacy_fingerprint(ACCOUNT_UID, case["raw"]) == case["v0.16.2"]
    assert migration._current_fingerprint(ACCOUNT_UID, case["raw"]) == case["v0.16.3"]
    # The provider must still produce what the migration assigns. If this
    # fails, the id recipe changed after v0.16.3 and rows re-keyed by 109 no
    # longer match what a sync computes.
    assert _txn_fingerprint(ACCOUNT_UID, cast(dict, case["raw"])) == case["v0.16.3"]


async def test_zero_reference_row_is_rekeyed_from_the_literal_id():
    # v0.16.2 stored `entry_reference.strip() or fingerprint`, so "0" was the id.
    raw = GOLDEN["zero_ref"]["raw"]
    assert migration._new_id(
        ACCOUNT_UID, raw, "0",
        type("Row", (), {"amount": Decimal("5.00"), "type": "debit", "date": date(2026, 8, 23)}),
    ) == GOLDEN["zero_ref"]["v0.16.3"]


async def test_rekeys_posted_rows_and_leaves_pending_rows(session, test_user, monkeypatch):
    account = await _account(session, test_user.id, "enable_banking")
    posted_raw = _payload()
    pending_raw = _payload(booking=None, amount="30.00", value="2026-09-02")
    zero_raw = _payload("0", amount="7.00")
    posted = _row(account, test_user.id, _legacy_id(posted_raw), posted_raw)
    posted.description = "Edited by the user"
    pending = _row(
        account, test_user.id, _legacy_id(pending_raw), pending_raw, status="pending",
    )
    zero = _row(account, test_user.id, "0", zero_raw)
    session.add_all([posted, pending, zero])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, posted.id) == _new_id(posted_raw)
    assert await _external_id(session, zero.id) == _new_id(zero_raw)
    # A pending hold is not final: it keeps its old id.
    assert await _external_id(session, pending.id) == _legacy_id(pending_raw)
    # Only the id changes; the user's edits stay.
    assert await session.scalar(
        select(Transaction.description).where(Transaction.id == posted.id)
    ) == "Edited by the user"


async def test_posted_row_settled_days_after_purchase_and_without_transaction_date(
    session, test_user, monkeypatch
):
    account = await _account(session, test_user.id, "enable_banking")
    settled_raw = _payload(booking="2026-08-23", value="2026-08-20", amount="21.00")
    no_date_raw = _payload(amount="22.00")
    del no_date_raw["transaction_date"]
    settled = _row(account, test_user.id, _legacy_id(settled_raw), settled_raw)
    no_date = _row(account, test_user.id, _legacy_id(no_date_raw), no_date_raw)
    session.add_all([settled, no_date])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, settled.id) == _new_id(settled_raw)
    assert await _external_id(session, no_date.id) == _new_id(no_date_raw)


async def test_same_old_id_for_different_counterparties_get_distinct_ids(
    session, test_user, monkeypatch
):
    account = await _account(session, test_user.id, "enable_banking")
    # No IBAN, so the old id cannot tell the two merchants apart.
    coffee_raw = _payload(amount="4.50", creditor="Coffee Shop")
    bakery_raw = _payload(amount="4.50", creditor="Bakery")
    assert _legacy_id(coffee_raw) == _legacy_id(bakery_raw)
    coffee = _row(account, test_user.id, _legacy_id(coffee_raw), coffee_raw)
    bakery = _row(account, test_user.id, _legacy_id(bakery_raw), bakery_raw)
    session.add_all([coffee, bakery])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, coffee.id) == _new_id(coffee_raw)
    assert await _external_id(session, bakery.id) == _new_id(bakery_raw)
    assert _new_id(coffee_raw) != _new_id(bakery_raw)


async def test_rows_that_are_not_legacy_are_left_alone(session, test_user, monkeypatch):
    eb = await _account(session, test_user.id, "enable_banking")
    other = await _account(session, test_user.id, "pluggy")
    current_raw = _payload(amount="1.00")
    current = _row(eb, test_user.id, _new_id(current_raw), current_raw)
    ref_raw = _payload("ref-1", amount="2.00")
    with_ref = _row(eb, test_user.id, "keyed-by-reference", ref_raw)
    # The id does not describe the payload: a later entry was merged into it.
    merged_raw = _payload(amount="3.00")
    merged = _row(eb, test_user.id, _legacy_id(_payload(amount="9.00")), merged_raw)
    # The payload no longer describes the row's amount.
    drift_raw = _payload(amount="4.00")
    drifted = _row(eb, test_user.id, _legacy_id(drift_raw), drift_raw)
    drifted.amount = Decimal("5.00")
    foreign_raw = _payload(amount="6.00")
    foreign = _row(other, test_user.id, _legacy_id(foreign_raw), foreign_raw)
    rows = [current, with_ref, merged, drifted, foreign]
    before = {row.id: row.external_id for row in rows}
    session.add_all(rows)
    await session.flush()

    await _upgrade(session, monkeypatch)

    for row in rows:
        assert await _external_id(session, row.id) == before[row.id]


async def test_same_old_id_with_different_transaction_dates_get_distinct_ids(
    session, test_user, monkeypatch
):
    account = await _account(session, test_user.id, "enable_banking")
    # Same old id (it ignores the transaction date), different new ids.
    first_raw = _payload(transaction_date="2026-08-29")
    second_raw = _payload(transaction_date="2026-08-30")
    assert _legacy_id(first_raw) == _legacy_id(second_raw)
    first = _row(account, test_user.id, _legacy_id(first_raw), first_raw)
    second = _row(account, test_user.id, _legacy_id(second_raw), second_raw)
    session.add_all([first, second])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, first.id) == _new_id(first_raw)
    assert await _external_id(session, second.id) == _new_id(second_raw)
    assert _new_id(first_raw) != _new_id(second_raw)


async def test_collisions_keep_the_old_id(session, test_user, monkeypatch):
    account = await _account(session, test_user.id, "enable_banking")
    # Two identical copies would get the same new id: neither is touched.
    dup_raw = _payload(amount="8.00")
    dup_a = _row(account, test_user.id, _legacy_id(dup_raw), dup_raw)
    dup_b = _row(account, test_user.id, _legacy_id(dup_raw), dup_raw)
    # A row whose new id is already stored in the account keeps its old id.
    taken_raw = _payload(amount="9.00")
    already = _row(account, test_user.id, _new_id(taken_raw), taken_raw)
    legacy = _row(account, test_user.id, _legacy_id(taken_raw), taken_raw)
    session.add_all([dup_a, dup_b, already, legacy])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, dup_a.id) == _legacy_id(dup_raw)
    assert await _external_id(session, dup_b.id) == _legacy_id(dup_raw)
    assert await _external_id(session, legacy.id) == _legacy_id(taken_raw)
    assert await _external_id(session, already.id) == _new_id(taken_raw)


async def test_rerun_changes_nothing(session, test_user, monkeypatch):
    account = await _account(session, test_user.id, "enable_banking")
    raw = _payload()
    row = _row(account, test_user.id, _legacy_id(raw), raw)
    session.add(row)
    await session.flush()

    await _upgrade(session, monkeypatch)
    once = await _external_id(session, row.id)
    await _upgrade(session, monkeypatch)

    assert once == _new_id(raw)
    assert await _external_id(session, row.id) == once


async def test_unreadable_payloads_are_skipped_without_aborting_the_upgrade(
    session, test_user, monkeypatch
):
    account = await _account(session, test_user.id, "enable_banking")
    good_raw = _payload(amount="11.00")
    good = _row(account, test_user.id, _legacy_id(good_raw), good_raw)
    bad_rows = []
    for amount, mutate in [
        ("21.00", lambda r: r["transaction_amount"].update(amount="not-a-number")),
        ("22.00", lambda r: r.update(transaction_amount=["22.00"])),
        ("23.00", lambda r: r["transaction_amount"].update(amount={"nested": 1})),
        ("24.00", lambda r: r.update(transaction_amount="24.00")),
    ]:
        raw = _payload("0", amount=amount)
        row = _row(account, test_user.id, "0", raw)
        mutate(raw)
        row.raw_data = raw
        bad_rows.append(row)
    not_a_dict = _row(account, test_user.id, "0", _payload("0", amount="25.00"))
    # Deliberately not a dict: the column is JSON, so a list can be stored.
    not_a_dict.raw_data = cast(Any, ["not", "a", "dict"])
    session.add_all([good, *bad_rows, not_a_dict])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, good.id) == _new_id(good_raw)
    for row in (*bad_rows, not_a_dict):
        assert await _external_id(session, row.id) == "0"


def _variant(**changes) -> dict:
    raw = _payload()
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        _variant(booking_date=None),
        _variant(booking_date=None, transaction_date=None),
        _variant(credit_debit_indicator="dbit"),
        _variant(credit_debit_indicator=None),
        _variant(credit_debit_indicator="CRDT"),
        _variant(transaction_amount={"currency": "EUR", "amount": "-12.00"}),
        _variant(transaction_amount={"currency": "EUR", "amount": "12.0"}),
    ],
    ids=[
        "no-booking-date", "value-date-only", "lowercase-indicator", "missing-indicator",
        "credit", "negative-amount", "short-decimal",
    ],
)
async def test_row_values_are_derived_like_the_provider(raw):
    """The migration's amount/type/date check agrees with the provider's parse."""
    parsed = EnableBankingProvider()._build_transaction(ACCOUNT_UID, raw, "posted", "auto")
    assert parsed is not None
    row = type(
        "Row", (), {"amount": parsed.amount, "type": parsed.type, "date": parsed.date},
    )
    assert migration._new_id(
        ACCOUNT_UID, raw, migration._legacy_fingerprint(ACCOUNT_UID, raw), row,
    ) == parsed.external_id
