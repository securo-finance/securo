"""A payee whose name carries a non-ASCII capital must be found, not reinserted.

Regression cover for #678. `get_or_create_payee` looked a counterparty up
with `lower(payees.name) = :name_lowered_in_python`, which silently assumes
Postgres and Python fold case the same way. They do not: `lower()` follows
the database's locale, so a cluster created with the C locale (a common
default in Kubernetes Postgres charts, unlike the `en_US.utf8` our compose
file gets) leaves every non-ASCII capital alone while Python folds it.

A name like "MÜLLER GmbH" therefore never matched itself. The first
transaction naming that counterparty inserted it, the second one missed the
lookup and inserted it again, and the unique constraint took down the whole
bank sync: no accounts, no transactions, connection marked `error`.

SQLite's `lower()` is ASCII-only for exactly the same reason, so the test
database reproduces the affected deployment without needing one.

The rule these tests pin: two transactions naming one counterparty create
one payee, whatever alphabet the bank writes it in.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.payee import Payee
from app.providers.base import AccountData, ConnectionData, TransactionData
from app.services.connection_service import handle_oauth_callback, sync_connection
from app.services.payee_service import get_or_create_payee

# The counterparty as the bank writes it. Any non-ASCII capital does it:
# an umlaut here, but a Cyrillic or accented name failed identically.
COUNTERPARTY = "MÜLLER GmbH"


def _txn(external_id: str) -> TransactionData:
    return TransactionData(
        external_id=external_id,
        description="SEPA TRANSFER",
        amount=Decimal("50"),
        date=date.today(),
        type="debit",
        currency="EUR",
        payee=COUNTERPARTY,
    )


def _accounts() -> list[AccountData]:
    """Two accounts at one bank, the shape the report failed on: the
    counterparty is shared, so the second account is where it collided."""
    return [
        AccountData(
            external_id="acc-1", name="Current", type="checking",
            balance=Decimal("100"), currency="EUR",
        ),
        AccountData(
            external_id="acc-2", name="Savings", type="savings",
            balance=Decimal("200"), currency="EUR",
        ),
    ]


async def _transactions_for(credentials, external_id, since=None, payee_source="auto"):
    return [_txn(f"{external_id}-t1"), _txn(f"{external_id}-t2")]


async def _payees(session: AsyncSession, workspace_id: uuid.UUID) -> list[Payee]:
    rows = await session.execute(
        select(Payee).where(Payee.workspace_id == workspace_id)
    )
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# the lookup itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_accented_name_finds_its_own_row(
    session: AsyncSession, test_user, test_workspace
):
    """The whole bug in two calls: the second one used to insert a duplicate."""
    first = await get_or_create_payee(
        session, test_user.id, COUNTERPARTY, workspace_id=test_workspace.id
    )
    second = await get_or_create_payee(
        session, test_user.id, COUNTERPARTY, workspace_id=test_workspace.id
    )
    await session.commit()

    assert first.id == second.id
    assert len(await _payees(session, test_workspace.id)) == 1


@pytest.mark.asyncio
async def test_case_folding_still_reuses_an_ascii_name(
    session: AsyncSession, test_user, test_workspace
):
    """Exact-match-first must not cost us the case folding that was there:
    a bank writing "Amazon" today and "AMAZON" tomorrow still means one
    counterparty."""
    first = await get_or_create_payee(
        session, test_user.id, "Amazon", workspace_id=test_workspace.id
    )
    second = await get_or_create_payee(
        session, test_user.id, "AMAZON", workspace_id=test_workspace.id
    )
    await session.commit()

    assert first.id == second.id
    assert len(await _payees(session, test_workspace.id)) == 1


@pytest.mark.asyncio
async def test_a_row_that_appears_between_lookup_and_insert_is_adopted(
    session: AsyncSession, test_user, test_workspace
):
    """The other way this constraint gets hit: two syncs of one connection
    overlapping, each having looked before the other inserted. Simulated by
    blinding the pre-checks, which is what a C-locale database did to them.
    The insert must lose gracefully and return the row that won, not fail a
    sync over a counterparty we were about to create anyway."""
    existing = await get_or_create_payee(
        session, test_user.id, COUNTERPARTY, workspace_id=test_workspace.id
    )
    await session.commit()

    real_scalar = AsyncSession.scalar
    seen = {"count": 0}

    async def blind_pre_checks(self, *args, **kwargs):
        seen["count"] += 1
        if seen["count"] <= 2:
            return None
        return await real_scalar(self, *args, **kwargs)

    with patch.object(AsyncSession, "scalar", blind_pre_checks):
        adopted = await get_or_create_payee(
            session, test_user.id, COUNTERPARTY, workspace_id=test_workspace.id
        )

    assert adopted.id == existing.id
    assert len(await _payees(session, test_workspace.id)) == 1


# ---------------------------------------------------------------------------
# the reported failure, end to end
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_connecting_a_bank_imports_a_shared_counterparty_once(
    session: AsyncSession, test_user, test_workspace
):
    """`POST /api/connections/oauth/callback` imports the first batch inline,
    so the connect itself is what died for providers that return accounts
    with the session."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="session-1",
        institution_name="Some Bank",
        credentials={"session_id": "x"},
        accounts=_accounts(),
    ))
    mock_provider.get_transactions = AsyncMock(side_effect=_transactions_for)

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        connection = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "code",
            "enablebanking", sync_assets=False,
        )

    assert connection.status == "active"
    payees = await _payees(session, test_workspace.id)
    assert [p.name for p in payees] == [COUNTERPARTY]


@pytest.mark.asyncio
async def test_sync_imports_a_shared_counterparty_once(
    session: AsyncSession, test_user, test_workspace
):
    """And the sync the report retried by hand, which failed the same way and
    rolled back every account with it."""
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        provider="enablebanking", external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Some Bank", credentials={"session_id": "x"},
        status="active", last_sync_at=None, created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"session_id": "x"})
    mock_provider.get_accounts = AsyncMock(return_value=_accounts())
    mock_provider.get_transactions = AsyncMock(side_effect=_transactions_for)

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        synced, _ = await sync_connection(
            session, connection.id, test_workspace.id, test_user.id
        )

    assert synced.status == "active"
    payees = await _payees(session, test_workspace.id)
    assert [p.name for p in payees] == [COUNTERPARTY]

    # The accounts and their transactions landed too: the duplicate key
    # aborted the whole transaction, which is why the report saw a bank
    # group with nothing under it.
    from app.models.account import Account
    from app.models.transaction import Transaction

    accounts = (await session.execute(
        select(Account).where(Account.connection_id == connection.id)
    )).scalars().all()
    assert len(accounts) == 2
    synced_tx = (await session.execute(
        select(Transaction).where(Transaction.source == "sync")
    )).scalars().all()
    assert len(synced_tx) == 4
