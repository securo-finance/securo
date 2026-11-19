"""A row's account-currency amount does not follow the display currency (ADR 0027).

Balances and bills read `amount_account` for a row in another currency than
its account. `amount_primary` follows the owner's display currency and only
equals it while the account is in that currency.
"""
import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.base import BankProvider, TransactionData
from app.services.backup_service import _legacy_account_amount
from app.services.connection_service import (
    _refresh_account_charge,
    _refresh_provider_values,
    _stamp_synced_primary_amount,
)
from app.services.fx_rate_service import stamp_primary_amount

ON = date(2025, 3, 10)


@pytest_asyncio.fixture
async def rates(session: AsyncSession, clean_db) -> None:
    # USD-based: 1 USD = 5 BRL = 0.9 EUR.
    for quote, rate in (("BRL", Decimal("5")), ("EUR", Decimal("0.9"))):
        session.add(FxRate(base_currency="USD", quote_currency=quote, date=ON, rate=rate, source="test"))
    await session.commit()


async def _set_display_currency(client: AsyncClient, headers, user: User, currency: str) -> None:
    prefs = {**(user.preferences or {}), "currency_display": currency}
    response = await client.patch("/api/users/me", headers=headers, json={"preferences": prefs})
    assert response.status_code == 200, response.text


async def _account(client: AsyncClient, headers, name: str, currency: str) -> str:
    response = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": name, "type": "checking", "balance": "0", "currency": currency},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _balance(client: AsyncClient, headers, account_id: str) -> Decimal:
    response = await client.get(f"/api/accounts/{account_id}", headers=headers)
    assert response.status_code == 200, response.text
    return Decimal(str(response.json()["current_balance"]))


async def _purchase(client: AsyncClient, headers, account_id: str, **extra) -> str:
    response = await client.post(
        "/api/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "description": "Online store",
            "amount": "100.00",
            "currency": "USD",
            "date": ON.isoformat(),
            "type": "debit",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_the_charged_amount_stays_in_the_balance_after_a_display_currency_switch(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _account(client, auth_headers, "Card", "BRL")
    # The user typed what the bank charged: R$ 545.10, not the market R$ 500.
    tx_id = await _purchase(client, auth_headers, account_id, amount_primary="545.10")
    assert await _balance(client, auth_headers, account_id) == Decimal("-545.10")

    # The display currency has nothing to do with what the BRL account holds,
    # even once the reporting stamp follows it to USD (#1381).
    await _set_display_currency(client, auth_headers, test_user, "USD")
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    tx.amount_primary, tx.fx_rate_used = Decimal("100.00"), Decimal("1")
    await session.commit()
    assert await _balance(client, auth_headers, account_id) == Decimal("-545.10")

    # Neither does an edit that leaves the amount alone.
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"description": "Store"}
    )
    assert response.status_code == 200, response.text
    assert await _balance(client, auth_headers, account_id) == Decimal("-545.10")


@pytest.mark.asyncio
async def test_an_account_in_another_currency_counts_in_its_own_currency(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _account(client, auth_headers, "Euro account", "EUR")
    tx_id = await _purchase(client, auth_headers, account_id)

    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("500.00")  # USD 100 in BRL, for reports
    assert tx.amount_account == Decimal("90.00")  # USD 100 in EUR, for the account
    # Before ADR 0027 the EUR balance summed the BRL figure: -500.
    assert await _balance(client, auth_headers, account_id) == Decimal("-90.00")


@pytest.mark.asyncio
async def test_moving_a_row_to_another_account_restamps_its_account_amount(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    brl_id = await _account(client, auth_headers, "Real", "BRL")
    usd_id = await _account(client, auth_headers, "Dollar", "USD")
    tx_id = await _purchase(client, auth_headers, brl_id)
    assert await _balance(client, auth_headers, brl_id) == Decimal("-500.00")

    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"account_id": usd_id}
    )
    assert response.status_code == 200, response.text
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    await session.refresh(tx)
    assert tx.amount_account is None  # now in its account's own currency
    assert await _balance(client, auth_headers, usd_id) == Decimal("-100.00")
    assert await _balance(client, auth_headers, brl_id) == Decimal("0.00")


@pytest.mark.asyncio
async def test_sync_keeps_the_bank_charge_when_the_account_is_not_in_the_primary_currency(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    """The provider's charge used to be dropped unless the account was in the
    user's primary currency; it is the account's figure either way."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,  # a BRL account
        description="Online store",
        amount=Decimal("100.00"),
        currency="USD",
        date=ON,
        type="debit",
        source="sync",
    )
    session.add(tx)
    await session.flush()

    # `_bank_converted_amount` gives None here: the account is not in USD.
    await _stamp_synced_primary_amount(session, test_user.id, tx, None, Decimal("545.10"))

    assert tx.amount_primary == Decimal("100.00")
    assert tx.amount_account == Decimal("545.10")


@pytest.mark.asyncio
async def test_a_backup_from_before_the_column_restores_the_same_balance(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _account(client, auth_headers, "Card", "BRL")
    await _purchase(client, auth_headers, account_id, amount_primary="545.10")

    archive = await client.get("/api/export/backup", headers=auth_headers)
    assert archive.status_code == 200
    legacy = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as source, zipfile.ZipFile(legacy, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.rsplit("/", 1)[-1] == "transactions.json":
                rows = json.loads(data)
                assert rows and all("amount_account" in row for row in rows)
                for row in rows:
                    del row["amount_account"]
                    del row["amount_account_source"]
                    if row["currency"] == "USD":
                        # A synced charge stamped while the display currency
                        # was USD; the payload kept the BRL figure.
                        row["source"] = "sync"
                        row["amount_primary"], row["fx_rate_used"] = "100.00", "1"
                        row["raw_data"] = {"amountInAccountCurrency": 545.1}
                data = json.dumps(rows).encode()
            target.writestr(item, data)

    restored = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", legacy.getvalue(), "application/zip")},
        data={"content": "both", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.text
    workspace_id = uuid.UUID(restored.json()["workspace_id"])
    copied = await session.scalar(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id, Transaction.currency == "USD"
        )
    )
    assert copied is not None
    assert copied.amount_account == Decimal("545.10")
    assert copied.amount_account_source == "bank"


def _bank_row(user: User, account: Account, **fields) -> Transaction:
    """A synced USD charge on the BRL `test_account`, R$ 545.10 per the bank."""
    return Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description="Online store",
        amount=Decimal("100.00"),
        currency="USD",
        date=ON,
        type="debit",
        source="sync",
        amount_account=Decimal("545.10"),
        amount_account_source="bank",
        **fields,
    )


@pytest.mark.asyncio
async def test_posting_a_pending_charge_keeps_the_bank_charge(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User,
    test_account: Account, rates,
):
    await _set_display_currency(client, auth_headers, test_user, "USD")
    # In USD the reporting stamp is the amount itself, at rate 1, which makes
    # posting heal it; the BRL charge has nothing to heal.
    tx = _bank_row(
        test_user, test_account, status="pending",
        amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
    )
    session.add(tx)
    await session.commit()

    response = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers, json={"status": "posted"}
    )
    assert response.status_code == 200, response.text
    await session.refresh(tx)
    assert tx.amount_account == Decimal("545.10")


@pytest.mark.asyncio
async def test_saving_the_same_account_keeps_the_bank_charge(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User,
    test_account: Account, rates,
):
    await _set_display_currency(client, auth_headers, test_user, "USD")
    tx = _bank_row(
        test_user, test_account,
        amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
    )
    session.add(tx)
    await session.commit()

    response = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers,
        json={"account_id": str(test_account.id), "description": "Store"},
    )
    assert response.status_code == 200, response.text
    await session.refresh(tx)
    assert tx.amount_account == Decimal("545.10")


@pytest.mark.asyncio
async def test_a_move_with_no_rate_into_the_new_account_leaves_it_unconverted(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    brl_id = await _account(client, auth_headers, "Real", "BRL")
    gbp_id = await _account(client, auth_headers, "Pound", "GBP")  # no GBP rate stored
    tx_id = await _purchase(client, auth_headers, brl_id)

    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"account_id": gbp_id}
    )
    assert response.status_code == 200, response.text
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    await session.refresh(tx)
    # Not the BRL 500 read as pounds: unconverted, so it counts as zero.
    assert tx.amount_account is None
    assert await _balance(client, auth_headers, gbp_id) == Decimal("0.00")


@pytest.mark.asyncio
async def test_healing_the_reporting_stamp_keeps_a_bank_charge_and_follows_a_copy(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    """What the FX healers do: restamp `amount_primary` with no account change."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "BRL"}
    charged = _bank_row(test_user, test_account)  # never got a primary stamp
    # A 1:1 fallback stamp, copied into the account amount when it was written.
    copied = _bank_row(
        test_user, test_account,
        amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
    )
    copied.amount_account, copied.amount_account_source = Decimal("100.00"), None
    session.add_all([charged, copied])
    await session.flush()

    for tx in (charged, copied):
        await stamp_primary_amount(session, test_user.id, tx, allow_fetch=False)

    assert charged.amount_primary == Decimal("500.00")
    assert charged.amount_account == Decimal("545.10")
    assert copied.amount_primary == Decimal("500.00")
    assert copied.amount_account == Decimal("500.00")


@pytest.mark.asyncio
async def test_sync_takes_a_corrected_bank_charge_under_the_same_amount(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    tx = _bank_row(
        test_user, test_account, raw_data={"id": "ext-1"},
        amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
    )
    session.add(tx)
    await session.flush()

    def _data(charged: str) -> TransactionData:
        return TransactionData(
            external_id="ext-1", description="Online store", amount=Decimal("100.00"),
            date=ON, type="debit", currency="USD",
            amount_in_account_currency=Decimal(charged),
        )

    # Like Pluggy: no parser for the stored payload, so the correction cannot
    # lean on what the previous payload said.
    provider = MagicMock(spec=BankProvider)
    provider.parse_raw_transaction.return_value = None
    await _refresh_provider_values(
        session, provider, tx, _data("550.00"),
        payee_source="auto", account_currency="BRL", user_currency="USD",
    )
    assert tx.amount_account == Decimal("550.00")


@pytest.mark.asyncio
async def test_a_bank_charge_equal_to_the_primary_stamp_is_kept_on_heal(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    """The bank charged exactly the foreign amount, so the row looks like a
    1:1 stamp; it is still the bank's figure and a heal must not touch it."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "BRL"}
    tx = _bank_row(
        test_user, test_account,
        amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
    )
    tx.amount_account = Decimal("100.00")
    session.add(tx)
    await session.flush()

    await stamp_primary_amount(session, test_user.id, tx, allow_fetch=False)

    assert tx.amount_primary == Decimal("500.00")
    assert tx.amount_account == Decimal("100.00")


@pytest.mark.asyncio
async def test_sync_does_not_replace_an_amount_the_user_typed(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    tx = _bank_row(test_user, test_account, raw_data={"id": "ext-1"})
    tx.amount_account_source = "user"
    session.add(tx)
    await session.flush()

    await _refresh_provider_values(
        session, MagicMock(spec=BankProvider), tx,
        TransactionData(
            external_id="ext-1", description="Online store", amount=Decimal("100.00"),
            date=ON, type="debit", currency="USD",
            amount_in_account_currency=Decimal("550.00"),
        ),
        payee_source="auto", account_currency="BRL", user_currency="BRL",
    )
    assert tx.amount_account == Decimal("545.10")


@pytest.mark.asyncio
async def test_a_pending_charge_settled_under_a_new_id_takes_the_final_charge(
    session: AsyncSession, test_user: User, test_workspace, rates
):
    """The hold and the posted charge come under different ids; the posted
    one carries the final figure the bank charged the account."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch

    from app.models.bank_connection import BankConnection
    from app.providers.base import AccountData
    from app.services.connection_service import sync_connection

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}", institution_name="Card bank",
        credentials={"token": "fake"}, status="active",
        last_sync_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    def _charge(external_id: str, status: str, charged: str) -> TransactionData:
        return TransactionData(
            external_id=external_id, description="ONLINE STORE", amount=Decimal("100.00"),
            date=ON, type="debit", currency="USD", status=status,
            amount_in_account_currency=Decimal(charged),
        )

    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "fake"})
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="card-1", name="Card", type="credit_card",
                    balance=Decimal("0"), currency="BRL"),
    ])
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        provider.get_transactions = AsyncMock(return_value=[_charge("hold-1", "pending", "545.10")])
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        provider.get_transactions = AsyncMock(return_value=[_charge("post-1", "posted", "550.00")])
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.scalars(
        select(Transaction).where(Transaction.description == "ONLINE STORE")
    )).all()
    assert len(rows) == 1
    assert rows[0].status == "posted"
    assert rows[0].amount_account == Decimal("550.00")


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # The stored payload says what the bank charged: that figure, whatever
        # the primary stamp was.
        ({"source": "sync", "amount_primary": Decimal("100.00"), "fx_rate_used": Decimal("1"),
          "raw_data": {"amountInAccountCurrency": -545.1}}, (Decimal("545.10"), "bank")),
        # A synced charge with no payload, at a real rate.
        ({"source": "sync", "amount_primary": Decimal("540.00"), "fx_rate_used": Decimal("5.4"),
          "raw_data": None}, (Decimal("540.00"), "bank")),
        # Exactly 1:1 with no payload: the old fallback, left for the healer.
        ({"source": "sync", "amount_primary": Decimal("100.00"), "fx_rate_used": Decimal("1"),
          "raw_data": None}, (Decimal("100.00"), None)),
        ({"source": "manual", "amount_primary": Decimal("100.00"), "fx_rate_used": Decimal("1")},
         (Decimal("100.00"), None)),
        ({"source": "manual", "amount_primary": Decimal("512.00"), "fx_rate_used": Decimal("5.12")},
         (Decimal("512.00"), "user")),
        ({"source": "manual", "amount_primary": None, "fx_rate_used": None}, (None, None)),
    ],
)
def test_an_older_backup_row_gets_the_account_amount_migration_124_gives(row, expected):
    assert _legacy_account_amount(row) == expected


@pytest.mark.asyncio
async def test_sync_leaves_an_ignored_row_alone(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    tx = _bank_row(test_user, test_account, raw_data={"id": "ext-1"}, is_ignored=True)
    session.add(tx)
    await session.flush()

    await _refresh_account_charge(
        session, tx,
        TransactionData(
            external_id="ext-2", description="Online store", amount=Decimal("100.00"),
            date=ON, type="debit", currency="USD", status="posted",
            amount_in_account_currency=Decimal("550.00"),
        ),
        account_currency="BRL", user_currency="BRL",
    )
    assert tx.amount_account == Decimal("545.10")


@pytest.mark.asyncio
async def test_a_bank_charge_equal_to_a_derived_amount_is_recorded_as_the_banks(
    session: AsyncSession, test_user: User, test_account: Account, rates
):
    tx = _bank_row(test_user, test_account, raw_data={"id": "ext-1"})
    tx.amount_account_source = None  # worked out from a rate, same figure
    session.add(tx)
    await session.flush()

    await _refresh_account_charge(
        session, tx,
        TransactionData(
            external_id="ext-1", description="Online store", amount=Decimal("100.00"),
            date=ON, type="debit", currency="USD",
            amount_in_account_currency=Decimal("545.10"),
        ),
        account_currency="BRL", user_currency="BRL",
    )
    assert tx.amount_account == Decimal("545.10")
    assert tx.amount_account_source == "bank"


@pytest.mark.asyncio
async def test_resending_the_same_amount_keeps_a_typed_account_amount(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _account(client, auth_headers, "Card", "BRL")
    tx_id = await _purchase(client, auth_headers, account_id, amount_primary="545.10")

    # An unchanged save: the typed charge stays.
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"amount": "100.00"}
    )
    assert response.status_code == 200, response.text
    assert await _balance(client, auth_headers, account_id) == Decimal("-545.10")

    # A real change of amount: the old charge no longer applies.
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"amount": "120.00"}
    )
    assert response.status_code == 200, response.text
    assert await _balance(client, auth_headers, account_id) == Decimal("-600.00")


@pytest.mark.asyncio
async def test_clearing_the_typed_conversion_keeps_a_bank_charge_and_drops_a_typed_one(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User,
    test_account: Account, rates,
):
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    charged = _bank_row(
        test_user, test_account,
        amount_primary=Decimal("545.10"), fx_rate_used=Decimal("5.451"),
    )
    session.add(charged)
    await session.commit()
    account_id = await _account(client, auth_headers, "Card", "BRL")
    typed_id = await _purchase(client, auth_headers, account_id, amount_primary="545.10")

    for tx_id in (charged.id, typed_id):
        response = await client.patch(
            f"/api/transactions/{tx_id}", headers=auth_headers,
            json={"amount_primary": None, "fx_rate_used": None},
        )
        assert response.status_code == 200, response.text

    await session.refresh(charged)
    assert charged.amount_primary == Decimal("500.00")  # the reporting value heals
    assert charged.amount_account == Decimal("545.10")  # the charge stays
    typed = await session.get(Transaction, uuid.UUID(typed_id))
    assert typed is not None
    await session.refresh(typed)
    assert typed.amount_account == Decimal("500.00")  # back to the rate
    assert typed.amount_account_source is None


@pytest.mark.asyncio
async def test_clearing_a_typed_conversion_with_no_rate_leaves_the_row_unconverted(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, clean_db
):
    """No USD/BRL rate is stored: the typed charge still goes, rather than
    surviving the clear as if the user had kept it."""
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _account(client, auth_headers, "Card", "BRL")
    tx_id = await _purchase(client, auth_headers, account_id, amount_primary="545.10")
    await _set_display_currency(client, auth_headers, test_user, "USD")

    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers,
        json={"amount_primary": None, "fx_rate_used": None},
    )
    assert response.status_code == 200, response.text
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    await session.refresh(tx)
    assert (tx.amount_account, tx.amount_account_source) == (None, None)
    assert await _balance(client, auth_headers, account_id) == Decimal("0.00")
