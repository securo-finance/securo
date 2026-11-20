"""Marking a transaction paid or not (issue #15).

A row that came from a statement — the bank sync, or a file the user imported —
is money that already moved, so it starts paid. A row the user types, a
transfer they record or the placeholder a recurring bill generates for a date
ahead starts unpaid. The mark is the user's: a later sync refreshes the provider
fields and leaves it exactly as the user left it. Nothing about totals, balances
or reports changes.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.services.connection_service import sync_connection

ACCOUNT_EXT_ID = "checking-1"


async def _manual_transaction(client, auth_headers, test_account: Account, **overrides):
    payload = {
        "account_id": str(test_account.id),
        "description": "Rent",
        "amount": "1200.00",
        "date": app_today().isoformat(),
        "type": "debit",
    }
    payload.update(overrides)
    response = await client.post("/api/transactions", json=payload, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _stored(session: AsyncSession, user_id, account: Account, source: str) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=f"Row from {source}",
        amount=Decimal("10.00"),
        currency=account.currency,
        date=app_today(),
        type="debit",
        source=source,
        status="posted",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    return tx


@pytest.mark.asyncio
async def test_a_manual_transaction_starts_unpaid(
    client, auth_headers, test_account: Account
):
    created = await _manual_transaction(client, auth_headers, test_account)

    assert created["is_paid"] is False


@pytest.mark.asyncio
async def test_a_synced_row_starts_paid(
    client, auth_headers, session: AsyncSession, test_user, test_account: Account
):
    tx = await _stored(session, test_user.id, test_account, "sync")
    await session.commit()

    read = await client.get(f"/api/transactions/{tx.id}", headers=auth_headers)

    assert read.status_code == 200
    assert read.json()["is_paid"] is True


@pytest.mark.asyncio
async def test_an_imported_row_starts_paid(
    client, auth_headers, session: AsyncSession, test_user, test_account: Account
):
    tx = await _stored(session, test_user.id, test_account, "import")
    await session.commit()

    read = await client.get(f"/api/transactions/{tx.id}", headers=auth_headers)

    assert read.status_code == 200
    assert read.json()["is_paid"] is True


@pytest.mark.asyncio
async def test_the_caller_may_create_a_row_already_paid(
    client, auth_headers, test_account: Account
):
    created = await _manual_transaction(client, auth_headers, test_account, is_paid=True)

    assert created["is_paid"] is True


@pytest.mark.asyncio
async def test_the_user_marks_a_row_paid_and_back(
    client, auth_headers, session: AsyncSession, test_user, test_account: Account
):
    tx = await _stored(session, test_user.id, test_account, "manual")
    await session.commit()

    paid = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_paid": True}, headers=auth_headers
    )
    assert paid.status_code == 200
    assert paid.json()["is_paid"] is True

    unpaid = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_paid": False}, headers=auth_headers
    )
    assert unpaid.json()["is_paid"] is False


@pytest.mark.asyncio
async def test_leaving_the_field_out_of_an_edit_keeps_the_mark(
    client, auth_headers, test_transactions: list[Transaction]
):
    tx = test_transactions[0]
    await client.patch(
        f"/api/transactions/{tx.id}", json={"is_paid": True}, headers=auth_headers
    )

    edited = await client.patch(
        f"/api/transactions/{tx.id}", json={"notes": "paid by transfer"}, headers=auth_headers
    )

    assert edited.status_code == 200
    assert edited.json()["is_paid"] is True


@pytest.mark.asyncio
async def test_list_returns_only_unpaid_rows_when_asked(
    client, auth_headers, session: AsyncSession, test_user, test_account: Account
):
    unpaid = await _manual_transaction(client, auth_headers, test_account)
    await _stored(session, test_user.id, test_account, "sync")
    await session.commit()

    response = await client.get(
        "/api/transactions?unpaid_only=true", headers=auth_headers
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [row["id"] for row in items] == [unpaid["id"]]


# --- a re-sync leaves the mark alone -------------------------------------


async def _connection_and_account(session: AsyncSession, user_id):
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=user_id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        connection_id=conn.id,
        external_id=ACCOUNT_EXT_ID,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    return conn, account


def _provider(description: str):
    provider = AsyncMock()
    provider.rereads_by_date = False
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id=ACCOUNT_EXT_ID, name="Checking", type="checking",
                    balance=Decimal("0"), currency="BRL"),
    ])
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])
    provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="tx-1",
            description=description,
            amount=Decimal("25.00"),
            date=app_today() - timedelta(days=1),
            type="debit",
            currency="BRL",
            status="posted",
        ),
    ])
    return provider


async def _sync(session, conn, workspace_id, user_id, provider):
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace_id, user_id)


@pytest.mark.asyncio
async def test_a_resync_keeps_the_mark_the_user_made(
    session: AsyncSession, test_user, test_workspace
):
    conn, _account = await _connection_and_account(session, test_user.id)
    await session.commit()

    await _sync(session, conn, test_workspace.id, test_user.id, _provider("GROCERY"))
    tx = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-1")
    )).scalar_one()
    # A statement row opens paid.
    assert tx.is_paid is True

    # The user corrects the bank: this row has not cleared yet.
    tx.is_paid = False
    await session.commit()

    await _sync(session, conn, test_workspace.id, test_user.id, _provider("GROCERY STORE"))
    refreshed = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-1")
    )).scalar_one()

    assert refreshed.description == "GROCERY STORE"
    assert refreshed.is_paid is False
