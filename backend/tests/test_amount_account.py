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

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.services.connection_service import _stamp_synced_primary_amount

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
