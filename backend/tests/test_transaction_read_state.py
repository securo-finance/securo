"""New/read state of a transaction (issue #636).

A row that arrives from outside (bank sync or a file import) starts unread so
the list can mark it as new; a row a person creates is already known and starts
read. Opening a row clears its marker, "mark all as read" clears the rest, and
`unread_only` narrows the list to the review queue.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.providers.base import AccountData, TransactionData
from app.services.connection_service import sync_connection
from app.services.import_service import import_transactions
from tests.test_connection_service import _make_connection

pytestmark = pytest.mark.asyncio


async def _account(client, headers) -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Checking", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create(client, headers, account_id, description: str, amount: str = "10.00") -> dict:
    resp = await client.post(
        "/api/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "description": description,
            "amount": amount,
            "date": date(2026, 1, 15).isoformat(),
            "type": "debit",
            "currency": "USD",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_unread(session, test_workspace, test_user, description: str) -> Transaction:
    """A row as sync or an import leaves it: present, not yet reviewed."""
    account = (await session.execute(
        select(Account).where(Account.workspace_id == test_workspace.id)
    )).scalars().first()
    if account is None:
        account = Account(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            name="Seed",
            type="checking",
            balance=Decimal("0"),
            currency="USD",
        )
        session.add(account)
        await session.flush()
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        currency="USD",
        date=date(2026, 1, 15),
        type="debit",
        source="sync",
        status="posted",
        is_read=False,
    )
    session.add(tx)
    await session.commit()
    return tx


async def test_a_transaction_a_person_creates_starts_read(
    client, auth_headers, test_workspace, test_user, session
):
    """Typing a transaction in is not "new" for the person who typed it."""
    account = await _account(client, auth_headers)
    created = await _create(client, auth_headers, account["id"], "Coffee")

    assert created["is_read"] is True

    listed = await client.get("/api/transactions", headers=auth_headers)
    assert listed.status_code == 200
    rows = [row for row in listed.json()["items"] if row["id"] == created["id"]]
    assert rows and rows[0]["is_read"] is True


async def test_a_synced_transaction_starts_unread(
    session, test_user: User, test_workspace
):
    conn = await _make_connection(session, test_user.id, "New Row Bank")
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[AccountData(
        external_id="new-row-acc", name="Checking", type="checking",
        balance=Decimal("0"), currency="USD",
    )])
    provider.get_transactions = AsyncMock(return_value=[TransactionData(
        external_id="fresh-charge", description="MARKET", amount=Decimal("-25.00"),
        date=date(2026, 1, 15), type="debit", currency="USD",
    )])
    patches = (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
    )
    for p in patches:
        p.start()
    try:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    finally:
        for p in patches:
            p.stop()

    synced = (await session.execute(
        select(Transaction).where(Transaction.external_id == "fresh-charge")
    )).scalar_one()
    assert synced.is_read is False


async def test_an_imported_transaction_starts_unread(
    session, test_user: User, test_workspace, test_account: Account
):
    from app.schemas.transaction import TransactionImport

    imported, skipped, _, _ = await import_transactions(
        session,
        test_workspace.id,
        test_user.id,
        test_account.id,
        [TransactionImport(
            description="Imported cafe",
            amount=Decimal("12.00"),
            date=date(2026, 1, 15),
            type="debit",
            currency=test_account.currency,
        )],
        "csv",
    )
    assert (imported, skipped) == (1, 0)

    row = (await session.execute(
        select(Transaction).where(Transaction.description == "Imported cafe")
    )).scalar_one()
    assert row.is_read is False


async def test_unread_only_lists_just_the_unreviewed_rows(
    client, auth_headers, test_workspace, test_user, session
):
    account = await _account(client, auth_headers)
    await _create(client, auth_headers, account["id"], "Already reviewed")
    unread = await _seed_unread(session, test_workspace, test_user, "Arrived overnight")

    response = await client.get(
        "/api/transactions", headers=auth_headers, params={"unread_only": "true"}
    )
    assert response.status_code == 200
    data = response.json()
    assert [row["id"] for row in data["items"]] == [str(unread.id)]
    # The count the UI shows on the filter follows the same set.
    assert data["total"] == 1

    everything = await client.get("/api/transactions", headers=auth_headers)
    assert everything.json()["total"] == 2


async def test_marking_one_transaction_read_is_idempotent(
    client, auth_headers, test_workspace, test_user, session
):
    unread = await _seed_unread(session, test_workspace, test_user, "One to open")

    first = await client.patch(
        f"/api/transactions/{unread.id}/read", headers=auth_headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["is_read"] is True

    second = await client.patch(
        f"/api/transactions/{unread.id}/read", headers=auth_headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["is_read"] is True

    remaining = await client.get(
        "/api/transactions", headers=auth_headers, params={"unread_only": "true"}
    )
    assert remaining.json()["total"] == 0


async def test_marking_read_does_not_reach_another_workspace(
    client, auth_headers, test_workspace, test_user, session
):
    other = Workspace(
        id=uuid.uuid4(),
        name="Someone else",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(other)
    await session.flush()
    account = Account(
        user_id=test_user.id,
        workspace_id=other.id,
        name="Their account",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    foreign = Transaction(
        user_id=test_user.id,
        workspace_id=other.id,
        account_id=account.id,
        description="Not mine",
        amount=Decimal("5.00"),
        currency="USD",
        date=date(2026, 1, 15),
        type="debit",
        source="sync",
        status="posted",
        is_read=False,
    )
    session.add(foreign)
    await session.commit()

    response = await client.patch(
        f"/api/transactions/{foreign.id}/read", headers=auth_headers
    )
    assert response.status_code == 404

    await session.refresh(foreign)
    assert foreign.is_read is False


async def test_mark_all_read_clears_only_this_workspace(
    client, auth_headers, test_workspace, test_user, session
):
    await _seed_unread(session, test_workspace, test_user, "First new row")
    await _seed_unread(session, test_workspace, test_user, "Second new row")

    other = Workspace(
        id=uuid.uuid4(),
        name="Another workspace",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(other)
    await session.flush()
    account = Account(
        user_id=test_user.id,
        workspace_id=other.id,
        name="Other account",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    untouched = Transaction(
        user_id=test_user.id,
        workspace_id=other.id,
        account_id=account.id,
        description="Stays new",
        amount=Decimal("5.00"),
        currency="USD",
        date=date(2026, 1, 15),
        type="debit",
        source="sync",
        status="posted",
        is_read=False,
    )
    session.add(untouched)
    await session.commit()

    response = await client.post("/api/transactions/read-all", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 2}

    left = await client.get(
        "/api/transactions", headers=auth_headers, params={"unread_only": "true"}
    )
    assert left.json()["total"] == 0

    await session.refresh(untouched)
    assert untouched.is_read is False

    again = await client.post("/api/transactions/read-all", headers=auth_headers)
    assert again.json() == {"updated": 0}


async def test_a_viewer_cannot_mark_rows_read(
    client, auth_headers, viewer_auth_headers, test_workspace, test_user, session
):
    unread = await _seed_unread(session, test_workspace, test_user, "Read-only view")

    one = await client.patch(
        f"/api/transactions/{unread.id}/read", headers=viewer_auth_headers
    )
    assert one.status_code == 403

    every = await client.post("/api/transactions/read-all", headers=viewer_auth_headers)
    assert every.status_code == 403

    await session.refresh(unread)
    assert unread.is_read is False
