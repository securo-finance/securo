"""Migration 102 on real PostgreSQL: which Enable Banking rows it re-keys."""
import importlib.util
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.enable_banking import EnableBankingProvider

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "102_backfill_enable_banking_external_id.py"
)
_SPEC = importlib.util.spec_from_file_location("eb_external_id_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


def _payload(entry_ref: str, amount: str, value_date: str = "2026-08-31") -> dict:
    return {
        "entry_reference": entry_ref,
        "transaction_amount": {"currency": "EUR", "amount": amount},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-31",
        "value_date": value_date,
        "remittance_information": ["E-COMM 1006,45 AT K3 26.08. 12:53"],
    }


def _new_id(raw: dict) -> str:
    parsed = EnableBankingProvider()._build_transaction("acc-1", raw, "posted", "auto")
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
        type="checking", balance=Decimal("0"), currency="EUR",
    )
    session.add(account)
    await session.flush()
    return account


def _row(account, user_id, external_id, raw, amount, *, source="sync") -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        external_id=external_id, description="E-COMM", amount=Decimal(amount),
        date=date(2026, 8, 31), type="debit", currency="EUR", source=source,
        status="posted", raw_data=raw, created_at=datetime.now(timezone.utc),
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


async def test_backfill_rekeys_synced_and_linked_rows_only(session, test_user, monkeypatch):
    eb_account = await _account(session, test_user.id, "enable_banking")
    other_account = await _account(session, test_user.id, "pluggy")

    synced_raw = _payload("ref-sync", "12.00")
    synced = _row(eb_account, test_user.id, "ref-sync", synced_raw, "12.00")
    # A local import that a sync linked to an EB transaction keeps its source.
    linked_raw = _payload("ref-linked", "40.00")
    linked = _row(eb_account, test_user.id, "ref-linked", linked_raw, "40.00", source="import")
    # \v used to be a literal "v" in the btrim set, which hid these refs.
    v_raw = _payload("v-ref-v", "7.00")
    v_ref = _row(eb_account, test_user.id, "v-ref-v", v_raw, "7.00")
    other = _row(other_account, test_user.id, "ref-other", _payload("ref-other", "5.00"), "5.00")
    session.add_all([synced, linked, v_ref, other])
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, synced.id) == _new_id(synced_raw)
    assert await _external_id(session, linked.id) == _new_id(linked_raw)
    assert await _external_id(session, v_ref.id) == _new_id(v_raw)
    assert await _external_id(session, other.id) == "ref-other"

    # Re-running changes nothing.
    await _upgrade(session, monkeypatch)
    assert await _external_id(session, synced.id) == _new_id(synced_raw)


async def test_backfill_skips_row_holding_a_colliding_payload(session, test_user, monkeypatch):
    """Under the old scheme the main charge matched the fee row and merged its
    payload in. Hashing that payload would give the fee row the main charge's
    id, so the next sync would re-insert the fee and never surface the charge.
    """
    account = await _account(session, test_user.id, "enable_banking")
    main_charge_raw = _payload("ref-1", "1006.45", value_date="2026-08-29")
    fee = _row(account, test_user.id, "ref-1", main_charge_raw, "0.55")
    session.add(fee)
    await session.flush()

    await _upgrade(session, monkeypatch)

    assert await _external_id(session, fee.id) == "ref-1"
