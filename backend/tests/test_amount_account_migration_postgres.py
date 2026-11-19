"""Migration 124 on real PostgreSQL: which rows get an account-currency amount."""
import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.services.fx_rate_service import stamp_primary_amount

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "124_transaction_amount_account.py"
)
_SPEC = importlib.util.spec_from_file_location("amount_account_migration", _MIGRATION_PATH)
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


def _row(
    account: Account, user_id, currency: str, primary: str | None,
    *, source: str = "manual", rate: str | None = None,
) -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        description=f"{currency} purchase", amount=Decimal("100.00"), currency=currency,
        date=date(2025, 3, 10), type="debit", source=source,
        amount_primary=Decimal(primary) if primary is not None else None,
        fx_rate_used=Decimal(rate) if rate is not None else None,
    )


async def test_backfill_copies_the_figure_the_balance_reads_today(session, test_user):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    foreign = _row(account, test_user.id, "USD", "545.10", source="sync", rate="5.451")
    typed = _row(account, test_user.id, "USD", "512.00", rate="5.12")
    fallback = _row(account, test_user.id, "GBP", "100.00", rate="1")
    # The bank really charged BRL 100 for USD 100: rate 1, but Pluggy's payload
    # says it was the bank's figure.
    charged_one_to_one = _row(account, test_user.id, "USD", "100.00", source="sync", rate="1")
    charged_one_to_one.raw_data = {"id": "p-1", "amountInAccountCurrency": 100}
    # A plugin charge with no stored payload: a synced row is the bank's figure.
    plugin_charge = _row(account, test_user.id, "USD", "540.00", source="sync", rate="5.4")
    # A synced row stamped exactly 1:1 with no payload: the old fallback, not a
    # charge, so it stays derived and heals like a manual one.
    synced_fallback = _row(account, test_user.id, "USD", "100.00", source="sync", rate="1")
    # Stamped while the display currency was USD: the primary amount says 100,
    # the payload says the card was charged BRL 545.10.
    charged_elsewhere = _row(account, test_user.id, "USD", "100.00", source="sync", rate="1")
    charged_elsewhere.raw_data = {"id": "p-2", "amountInAccountCurrency": -545.1}
    lower_case_same = _row(account, test_user.id, "brl", "100.00")
    unconverted = _row(account, test_user.id, "EUR", None)
    session.add_all([
        foreign, typed, fallback, charged_one_to_one, plugin_charge, synced_fallback,
        charged_elsewhere, lower_case_same, unconverted,
    ])
    await session.flush()

    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's raw SQL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await conn.execute(text(migration.BACKFILL_SQL))

    stored = {
        row.id: (row.amount_account, row.amount_account_source)
        for row in (await session.execute(
            select(
                Transaction.id, Transaction.amount_account, Transaction.amount_account_source
            ).where(Transaction.account_id == account.id)
        )).all()
    }
    assert stored[foreign.id] == (Decimal("545.10"), "bank")
    assert stored[typed.id] == (Decimal("512.00"), "user")
    # The 1:1 fallback #353 stopped writing stays derived, so the healer fixes it.
    assert stored[fallback.id] == (Decimal("100.00"), None)
    assert stored[charged_one_to_one.id] == (Decimal("100.00"), "bank")
    assert stored[plugin_charge.id] == (Decimal("540.00"), "bank")
    assert stored[synced_fallback.id] == (Decimal("100.00"), None)
    assert stored[charged_elsewhere.id] == (Decimal("545.10"), "bank")
    # Same currency as the account, whatever the case: balances read `amount`.
    assert stored[lower_case_same.id] == (None, None)
    # Not converted yet: stays NULL, as the balance already counted it as zero.
    assert stored[unconverted.id] == (None, None)

    # Then the reporting stamps are healed at today's rates: every bank charge
    # stays, the old fallbacks follow the real rate.
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "BRL"}
    for quote, rate in (("BRL", Decimal("5")), ("GBP", Decimal("0.8"))):
        session.add(FxRate(
            base_currency="USD", quote_currency=quote, date=date(2025, 3, 10),
            rate=rate, source="test",
        ))
    await session.flush()
    for row in (charged_one_to_one, plugin_charge, synced_fallback, charged_elsewhere, fallback):
        await session.refresh(row)
        await stamp_primary_amount(session, test_user.id, row, allow_fetch=False)
    assert charged_one_to_one.amount_primary == Decimal("500.00")
    assert charged_one_to_one.amount_account == Decimal("100.00")
    assert plugin_charge.amount_primary == Decimal("500.00")
    assert plugin_charge.amount_account == Decimal("540.00")
    assert synced_fallback.amount_primary == Decimal("500.00")
    assert synced_fallback.amount_account == Decimal("500.00")
    assert charged_elsewhere.amount_account == Decimal("545.10")
    assert fallback.amount_primary == Decimal("625.00")
    assert fallback.amount_account == Decimal("625.00")
