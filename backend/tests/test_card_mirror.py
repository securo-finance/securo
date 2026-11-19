"""A card debited on the spot, synced as an account of its own (#1460).

The bank lists the card next to the account it draws from, and returns
every purchase on both. The card's copy is set aside (ignored) so the
purchase counts once; everything that does not look exactly like that
is left alone.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.services.card_mirror_service import ignore_mirrored_card_charges
from app.services.connection_service import sync_connection

TODAY = app_today()


async def _connection(session: AsyncSession, user_id, workspace_id) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        provider="test", external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Bank", credentials={"token": "fake"},
        status="active", settings={},
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    return conn


async def _account(
    session, user_id, workspace_id, conn, kind, *, balance="0", external_id=None
) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        connection_id=conn.id, external_id=external_id or uuid.uuid4().hex,
        name=kind, type=kind, balance=Decimal(balance), currency="EUR",
    )
    session.add(account)
    await session.flush()
    return account


def _tx(session, account, external_id, amount, *, days_ago=0, kind="debit", source="sync"):
    tx = Transaction(
        id=uuid.uuid4(), user_id=account.user_id, workspace_id=account.workspace_id,
        account_id=account.id, external_id=external_id,
        description=f"CARD PURCHASE {external_id}", amount=Decimal(amount),
        currency="EUR", date=TODAY - timedelta(days=days_ago),
        effective_date=TODAY - timedelta(days=days_ago),
        type=kind, source=source, status="posted",
    )
    session.add(tx)
    return tx


async def _ignore(session, conn, candidate_ids, *, skipped=()) -> set:
    """Run the matcher as a sync would: every account refreshed but `skipped`."""
    refreshed = set((await session.execute(
        select(Account.id).where(Account.connection_id == conn.id)
    )).scalars()) - {account.id for account in skipped}
    return await ignore_mirrored_card_charges(session, conn.id, candidate_ids, refreshed)


@pytest.fixture
async def bank(session, test_user, test_workspace):
    conn = await _connection(session, test_user.id, test_workspace.id)
    checking = await _account(session, test_user.id, test_workspace.id, conn, "checking")
    card = await _account(session, test_user.id, test_workspace.id, conn, "credit_card")
    return conn, checking, card


@pytest.mark.asyncio
async def test_card_copy_of_a_purchase_is_ignored_and_account_copy_kept(session, bank):
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00")
    on_account = _tx(session, checking, "acct-1", "25.00")
    # The bank sometimes reports the copy a day apart, or under the same id.
    late_card = _tx(session, card, "same-id", "7.44", days_ago=1)
    late_account = _tx(session, checking, "same-id", "7.44")
    await session.flush()

    touched = await _ignore(
        session, conn, [on_card.id, on_account.id, late_card.id, late_account.id]
    )

    assert touched == {card.id}
    assert on_card.is_ignored and late_card.is_ignored
    assert not on_account.is_ignored and not late_account.is_ignored


@pytest.mark.asyncio
async def test_card_with_a_balance_is_a_real_credit_card_and_is_left_alone(
    session, test_user, test_workspace
):
    conn = await _connection(session, test_user.id, test_workspace.id)
    checking = await _account(session, test_user.id, test_workspace.id, conn, "checking")
    card = await _account(
        session, test_user.id, test_workspace.id, conn, "credit_card", balance="300"
    )
    on_card = _tx(session, card, "card-1", "25.00")
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [on_card.id, on_account.id]) == set()
    assert not on_card.is_ignored


@pytest.mark.asyncio
async def test_two_card_purchases_against_one_account_debit_ignore_nothing(session, bank):
    conn, checking, card = bank
    first = _tx(session, card, "card-1", "25.00")
    second = _tx(session, card, "card-2", "25.00")
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [first.id, second.id, on_account.id]) == set()
    assert not first.is_ignored and not second.is_ignored


@pytest.mark.asyncio
async def test_nothing_is_ignored_outside_the_rules(session, test_user, test_workspace, bank):
    conn, checking, card = bank
    other_conn = await _connection(session, test_user.id, test_workspace.id)
    elsewhere = await _account(
        session, test_user.id, test_workspace.id, other_conn, "checking"
    )
    # Another connection's account, a different amount, too far apart, a
    # refund (credit) and a row typed by hand: none is the card's twin.
    rows = [
        _tx(session, card, "c-other-conn", "10.00"),
        _tx(session, elsewhere, "a-other-conn", "10.00"),
        _tx(session, card, "c-amount", "11.00"),
        _tx(session, checking, "a-amount", "11.01"),
        _tx(session, card, "c-far", "12.00", days_ago=5),
        _tx(session, checking, "a-far", "12.00"),
        _tx(session, card, "c-refund", "13.00", kind="credit"),
        _tx(session, checking, "a-refund", "13.00", kind="credit"),
        _tx(session, card, "c-manual", "14.00"),
        _tx(session, checking, "a-manual", "14.00", source="manual"),
    ]
    await session.flush()

    assert await _ignore(session, conn, [tx.id for tx in rows]) == set()
    assert not any(tx.is_ignored for tx in rows)


@pytest.mark.asyncio
async def test_two_old_rows_are_not_paired_by_a_later_sync(session, bank):
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00")
    _tx(session, checking, "acct-1", "25.00")
    unrelated_new = _tx(session, checking, "acct-2", "99.00")
    await session.flush()

    assert await _ignore(session, conn, [unrelated_new.id]) == set()
    assert not on_card.is_ignored


@pytest.mark.asyncio
async def test_a_paid_off_card_keeps_its_purchase(session, bank):
    # A regular card whose bill held one purchase, imported after it was
    # paid: it reads zero, and the payment leaving the account has the
    # purchase's amount. The card shows the payment arriving, so the
    # account's debit is that payment, not a copy of the purchase.
    conn, checking, card = bank
    purchase = _tx(session, card, "card-1", "100.00")
    payment_in = _tx(session, card, "card-pay", "100.00", kind="credit")
    payment_out = _tx(session, checking, "acct-pay", "100.00")
    await session.flush()

    assert await _ignore(
        session, conn, [purchase.id, payment_in.id, payment_out.id]
    ) == set()
    assert not purchase.is_ignored


@pytest.mark.asyncio
async def test_one_account_debit_never_sets_aside_two_purchases(session, bank):
    # The first sync paired the card copy with the account's debit. A
    # second purchase of the same amount on the card is a purchase of its
    # own: the account's debit is already taken.
    conn, checking, card = bank
    first = _tx(session, card, "card-1", "25.00", days_ago=1)
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()
    assert await _ignore(session, conn, [first.id, on_account.id]) == {card.id}

    second = _tx(session, card, "card-2", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [second.id]) == set()
    assert first.is_ignored and not second.is_ignored


@pytest.mark.asyncio
async def test_a_card_row_brought_back_stays_back(session, bank):
    # Paired with an account debit two days later, then brought back by
    # the person. A later sync writes an account debit on the card row's
    # own date: closer, but the card row stays as the person set it.
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00", days_ago=2)
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()
    assert await _ignore(session, conn, [on_card.id, on_account.id]) == {card.id}
    on_card.is_ignored = False

    closer = _tx(session, checking, "acct-2", "25.00", days_ago=2)
    await session.flush()

    assert await _ignore(session, conn, [closer.id]) == set()
    assert not on_card.is_ignored


@pytest.mark.asyncio
async def test_a_card_row_whose_copy_arrives_later_is_still_paired(session, bank):
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00", days_ago=1)
    await session.flush()
    assert await _ignore(session, conn, [on_card.id]) == set()

    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [on_account.id]) == {card.id}
    assert on_card.is_ignored


@pytest.mark.asyncio
async def test_a_card_the_sync_skipped_is_left_alone(session, bank):
    # The provider skipped the card (throttled): its zero balance is from
    # an earlier run and may no longer hold.
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00")
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(
        session, conn, [on_card.id, on_account.id], skipped=[card]
    ) == set()
    assert not on_card.is_ignored


@pytest.mark.asyncio
async def test_sync_counts_a_mirrored_card_purchase_once(
    session, test_user, test_workspace
):
    conn = await _connection(session, test_user.id, test_workspace.id)
    await session.commit()

    def purchase(external_id: str, description: str) -> TransactionData:
        return TransactionData(
            external_id=external_id, description=description,
            amount=Decimal("25.00"), date=TODAY, type="debit",
            currency="EUR", status="posted",
        )

    feeds = {
        "acct": [purchase("acct-1", "CARD 07/11 SHOP")],
        "card": [purchase("card-1", "CARD 07/11 SHOP CB")],
    }
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="acct", name="Checking", type="checking",
                    balance=Decimal("975.00"), currency="EUR"),
        AccountData(external_id="card", name="Debit card", type="credit_card",
                    balance=Decimal("0"), currency="EUR"),
    ])
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])
    provider.get_transactions = AsyncMock(
        side_effect=lambda credentials, account_id, *args, **kwargs: feeds[account_id]
    )

    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        # Twice: the second sync returns the same rows and must neither
        # bring the card copy back nor insert it again.
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    card = (await session.execute(
        select(Account).where(Account.connection_id == conn.id, Account.external_id == "card")
    )).scalar_one()
    found = list((await session.execute(
        select(Transaction).where(Transaction.external_id.in_(["acct-1", "card-1"]))
    )).scalars())
    assert len(found) == 2
    rows = {tx.external_id: tx for tx in found}
    assert rows["card-1"].is_ignored
    assert not rows["acct-1"].is_ignored

    # The card owes nothing, and with its copy ignored no opening balance
    # is invented to make up for it.
    counted = (await session.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.account_id == card.id,
            Transaction.is_ignored.is_(False),
        )
    )).scalar_one()
    assert counted == 0
