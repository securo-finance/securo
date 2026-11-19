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
from app.models.reconciliation import ReconciliationEvent
from app.models.transaction import Transaction
from app.providers.base import AccountData, ProviderRateLimited, TransactionData
from app.services.card_mirror_service import STRATEGY, ignore_mirrored_card_charges
from app.services.connection_service import sync_connection

TODAY = app_today()
AN_HOUR_AGO = datetime.now(timezone.utc) - timedelta(hours=1)


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


def _tx(
    session, account, external_id, amount, *, days_ago=0, kind="debit",
    source="sync", currency="EUR", created_at=None,
):
    tx = Transaction(
        id=uuid.uuid4(), user_id=account.user_id, workspace_id=account.workspace_id,
        account_id=account.id, external_id=external_id,
        description=f"CARD PURCHASE {external_id}", amount=Decimal(amount),
        currency=currency, date=TODAY - timedelta(days=days_ago),
        effective_date=TODAY - timedelta(days=days_ago),
        type=kind, source=source, status="posted",
    )
    if created_at is not None:
        tx.created_at = created_at
    session.add(tx)
    return tx


async def _ignore(session, conn, candidate_ids, *, skipped=()) -> set:
    """Run the matcher as a sync would: every feed read whole but `skipped`."""
    complete = set((await session.execute(
        select(Account.id).where(Account.connection_id == conn.id)
    )).scalars()) - {account.id for account in skipped}
    return await ignore_mirrored_card_charges(session, conn, candidate_ids, complete)


async def _claims(session) -> list[tuple]:
    """(card row, account row) for every pair the matcher recorded."""
    return list((await session.execute(
        select(ReconciliationEvent.transaction_id, ReconciliationEvent.expectation_id)
        .where(ReconciliationEvent.strategy_id == STRATEGY)
    )).all())


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
    # Each pair is in the history: the card row against the account row.
    assert sorted(await _claims(session)) == sorted(
        [(on_card.id, on_account.id), (late_card.id, late_account.id)]
    )


@pytest.mark.asyncio
async def test_one_pair_alone_is_not_enough(session, bank):
    # A regular card whose one purchase was paid at once, with the payment
    # missing from the card's feed, looks exactly like this. A card debited
    # on the spot shows it again on its next purchase.
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00")
    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [on_card.id, on_account.id]) == set()
    assert not on_card.is_ignored

    second_card = _tx(session, card, "card-2", "9.90")
    second_account = _tx(session, checking, "acct-2", "9.90")
    await session.flush()

    assert await _ignore(session, conn, [second_card.id, second_account.id]) == {card.id}
    assert on_card.is_ignored and second_card.is_ignored


@pytest.mark.asyncio
async def test_card_with_a_balance_is_a_real_credit_card_and_is_left_alone(
    session, test_user, test_workspace
):
    conn = await _connection(session, test_user.id, test_workspace.id)
    checking = await _account(session, test_user.id, test_workspace.id, conn, "checking")
    card = await _account(
        session, test_user.id, test_workspace.id, conn, "credit_card", balance="300"
    )
    rows = [
        _tx(session, card, "card-1", "25.00"),
        _tx(session, checking, "acct-1", "25.00"),
        _tx(session, card, "card-2", "9.90"),
        _tx(session, checking, "acct-2", "9.90"),
    ]
    await session.flush()

    assert await _ignore(session, conn, [tx.id for tx in rows]) == set()
    assert not any(tx.is_ignored for tx in rows)


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
    loan = await _account(session, test_user.id, test_workspace.id, conn, "loan")
    # Another connection's account, a different amount, too far apart, a
    # refund (credit), a row typed by hand, another currency and a loan
    # repayment: none is the card's twin.
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
        _tx(session, card, "c-currency", "15.00"),
        _tx(session, checking, "a-currency", "15.00", currency="USD"),
        _tx(session, card, "c-loan", "16.00"),
        _tx(session, loan, "a-loan", "16.00"),
    ]
    await session.flush()

    assert await _ignore(session, conn, [tx.id for tx in rows]) == set()
    assert not any(tx.is_ignored for tx in rows)


@pytest.mark.asyncio
async def test_history_written_before_the_matcher_ran_is_left_as_it_was(session, bank):
    conn, checking, card = bank
    old = [
        _tx(session, card, "card-1", "25.00", created_at=AN_HOUR_AGO),
        _tx(session, checking, "acct-1", "25.00", created_at=AN_HOUR_AGO),
        _tx(session, card, "card-2", "9.90", created_at=AN_HOUR_AGO),
        _tx(session, checking, "acct-2", "9.90", created_at=AN_HOUR_AGO),
    ]
    unrelated_new = _tx(session, checking, "acct-3", "99.00")
    await session.flush()

    assert await _ignore(session, conn, [unrelated_new.id]) == set()
    assert not any(tx.is_ignored for tx in old)
    # And on the next run too: the history stays as it was.
    assert await _ignore(session, conn, []) == set()
    assert not any(tx.is_ignored for tx in old)


@pytest.mark.asyncio
async def test_a_paid_off_card_keeps_its_purchase(session, bank):
    # A regular card whose bill held one purchase, imported after it was
    # paid: it reads zero, and the payment leaving the account has the
    # purchase's amount. The card shows the payment arriving, here five
    # days after it left the account, so that debit is the payment, not
    # a copy of the purchase. A payment in another currency proves nothing.
    conn, checking, card = bank
    rows = [
        _tx(session, card, "card-1", "100.00", days_ago=6),
        _tx(session, card, "card-pay", "100.00", kind="credit"),
        _tx(session, checking, "acct-pay", "100.00", days_ago=5),
        _tx(session, card, "card-2", "25.00"),
        _tx(session, card, "card-usd", "25.00", kind="credit", currency="USD"),
        _tx(session, checking, "acct-2", "25.00"),
        _tx(session, card, "card-3", "9.90"),
        _tx(session, checking, "acct-3", "9.90"),
    ]
    await session.flush()
    purchase, _, _, second, _, _, third, _ = rows

    assert await _ignore(session, conn, [tx.id for tx in rows]) == {card.id}
    assert not purchase.is_ignored
    assert second.is_ignored and third.is_ignored


@pytest.mark.asyncio
async def test_one_account_debit_never_sets_aside_two_purchases(session, bank):
    # The first sync paired the card copy with the account's debit. A
    # second purchase of the same amount on the card is a purchase of its
    # own: the account's debit is already taken.
    conn, checking, card = bank
    first = _tx(session, card, "card-1", "25.00", days_ago=1)
    on_account = _tx(session, checking, "acct-1", "25.00")
    other = [_tx(session, card, "card-2", "9.90"), _tx(session, checking, "acct-2", "9.90")]
    await session.flush()
    assert await _ignore(
        session, conn, [first.id, on_account.id] + [tx.id for tx in other]
    ) == {card.id}

    second = _tx(session, card, "card-3", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [second.id]) == set()
    assert first.is_ignored and not second.is_ignored


@pytest.mark.asyncio
async def test_a_pair_is_not_worked_out_again_when_closer_rows_arrive(session, bank):
    # C1 (two days ago) paired with A1 (yesterday). Then A2 lands on C1's
    # own date and C2 on A1's. Read from the pool alone, C1 would now name
    # A2 and leave A1 free for C2: two purchases set aside by one debit.
    # The pair made stays made, so A1 backs C1 only.
    conn, checking, card = bank
    c1 = _tx(session, card, "card-1", "25.00", days_ago=2)
    a1 = _tx(session, checking, "acct-1", "25.00", days_ago=1)
    other = [_tx(session, card, "card-9", "9.90"), _tx(session, checking, "acct-9", "9.90")]
    await session.flush()
    await _ignore(session, conn, [c1.id, a1.id] + [tx.id for tx in other])
    assert c1.is_ignored

    a2 = _tx(session, checking, "acct-2", "25.00", days_ago=2)
    c2 = _tx(session, card, "card-2", "25.00", days_ago=1)
    await session.flush()
    await _ignore(session, conn, [a2.id, c2.id])

    claims = await _claims(session)
    backing = [account_tx for card_tx, account_tx in claims]
    assert len(backing) == len(set(backing))
    assert (c1.id, a1.id) in claims
    # Every ignored card row is backed by its own account debit.
    ignored = {tx.id for tx in (c1, c2) if tx.is_ignored}
    assert ignored == {card_tx for card_tx, _ in claims} & {c1.id, c2.id}


@pytest.mark.asyncio
async def test_a_card_row_brought_back_stays_back(session, bank):
    # Paired with an account debit two days later, then brought back by
    # the person. A later sync writes an account debit on the card row's
    # own date: closer, but the card row stays as the person set it.
    conn, checking, card = bank
    on_card = _tx(session, card, "card-1", "25.00", days_ago=2)
    on_account = _tx(session, checking, "acct-1", "25.00")
    other = [_tx(session, card, "card-9", "9.90"), _tx(session, checking, "acct-9", "9.90")]
    await session.flush()
    assert await _ignore(
        session, conn, [on_card.id, on_account.id] + [tx.id for tx in other]
    ) == {card.id}
    on_card.is_ignored = False

    closer = _tx(session, checking, "acct-2", "25.00", days_ago=2)
    await session.flush()

    assert await _ignore(session, conn, [closer.id]) == set()
    assert not on_card.is_ignored


@pytest.mark.asyncio
async def test_a_card_row_whose_copy_arrives_later_is_still_paired(session, bank):
    conn, checking, card = bank
    other = [_tx(session, card, "card-9", "9.90"), _tx(session, checking, "acct-9", "9.90")]
    on_card = _tx(session, card, "card-1", "25.00", days_ago=1)
    await session.flush()
    assert await _ignore(session, conn, [on_card.id] + [tx.id for tx in other]) == set()

    on_account = _tx(session, checking, "acct-1", "25.00")
    await session.flush()

    assert await _ignore(session, conn, [on_account.id]) == {card.id}
    assert on_card.is_ignored


@pytest.mark.asyncio
async def test_a_card_the_sync_skipped_is_left_alone(session, bank):
    # The card's feed did not come back whole (throttled, cut short): its
    # zero balance says nothing about a payment it may be missing.
    conn, checking, card = bank
    rows = [
        _tx(session, card, "card-1", "25.00"),
        _tx(session, checking, "acct-1", "25.00"),
        _tx(session, card, "card-2", "9.90"),
        _tx(session, checking, "acct-2", "9.90"),
    ]
    await session.flush()

    assert await _ignore(session, conn, [tx.id for tx in rows], skipped=[card]) == set()
    assert not any(tx.is_ignored for tx in rows)


@pytest.mark.asyncio
async def test_a_pair_put_off_by_a_skipped_card_is_decided_on_a_later_run(session, bank):
    # Sync 1 writes the card copies; sync 2 writes the account copies but
    # skips the card; sync 3 reads both and writes nothing new.
    conn, checking, card = bank
    on_card = [_tx(session, card, "card-1", "25.00"), _tx(session, card, "card-2", "9.90")]
    await session.flush()
    assert await _ignore(session, conn, [tx.id for tx in on_card]) == set()

    on_account = [
        _tx(session, checking, "acct-1", "25.00"),
        _tx(session, checking, "acct-2", "9.90"),
    ]
    await session.flush()
    assert await _ignore(
        session, conn, [tx.id for tx in on_account], skipped=[card]
    ) == set()

    assert await _ignore(session, conn, []) == {card.id}
    assert all(tx.is_ignored for tx in on_card)
    assert not any(tx.is_ignored for tx in on_account)


def _provider(accounts: list[AccountData], feeds: dict) -> AsyncMock:
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=accounts)
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])

    async def transactions(credentials, account_id, *args, **kwargs):
        feed = feeds[account_id]
        if isinstance(feed, Exception):
            raise feed
        return feed

    provider.get_transactions = AsyncMock(side_effect=transactions)
    return provider


def _purchase(external_id: str, description: str, amount: str = "25.00") -> TransactionData:
    return TransactionData(
        external_id=external_id, description=description,
        amount=Decimal(amount), date=TODAY, type="debit",
        currency="EUR", status="posted",
    )


async def _sync(session, conn, workspace_id, user_id, provider) -> None:
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace_id, user_id)


ACCOUNTS = [
    AccountData(external_id="acct", name="Checking", type="checking",
                balance=Decimal("965.10"), currency="EUR"),
    AccountData(external_id="card", name="Debit card", type="credit_card",
                balance=Decimal("0"), currency="EUR"),
]


@pytest.mark.asyncio
async def test_sync_counts_a_mirrored_card_purchase_once(
    session, test_user, test_workspace
):
    conn = await _connection(session, test_user.id, test_workspace.id)
    await session.commit()

    provider = _provider(ACCOUNTS, {
        "acct": [_purchase("acct-1", "CARD 07/11 SHOP"),
                 _purchase("acct-2", "CARD 07/11 BAKERY", "9.90")],
        "card": [_purchase("card-1", "CARD 07/11 SHOP CB"),
                 _purchase("card-2", "CARD 07/11 BAKERY CB", "9.90")],
    })
    # Twice: the second sync returns the same rows and must neither bring
    # the card copies back nor insert them again.
    await _sync(session, conn, test_workspace.id, test_user.id, provider)
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    card = (await session.execute(
        select(Account).where(Account.connection_id == conn.id, Account.external_id == "card")
    )).scalar_one()
    found = list((await session.execute(
        select(Transaction).where(
            Transaction.external_id.in_(["acct-1", "acct-2", "card-1", "card-2"])
        )
    )).scalars())
    assert len(found) == 4
    rows = {tx.external_id: tx for tx in found}
    assert rows["card-1"].is_ignored and rows["card-2"].is_ignored
    assert not rows["acct-1"].is_ignored and not rows["acct-2"].is_ignored

    # The card owes nothing, and with its copies ignored no opening balance
    # is invented to make up for them.
    counted = (await session.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.account_id == card.id,
            Transaction.is_ignored.is_(False),
        )
    )).scalar_one()
    assert counted == 0


@pytest.mark.asyncio
async def test_sync_leaves_a_card_alone_while_its_feed_is_throttled(
    session, test_user, test_workspace
):
    # Sync 1 brings two card purchases. In sync 2 the card's balance comes
    # back (zero: the bill was paid) but its transactions are throttled,
    # so the payment is not in yet, while the account shows two debits of
    # the same amounts. Those debits may be the payments: nothing is set
    # aside until the card's own feed can be read.
    conn = await _connection(session, test_user.id, test_workspace.id)
    await session.commit()

    first = _provider(ACCOUNTS, {
        "acct": [],
        "card": [_purchase("card-1", "SHOP"), _purchase("card-2", "BAKERY", "9.90")],
    })
    await _sync(session, conn, test_workspace.id, test_user.id, first)

    second = _provider(ACCOUNTS, {
        "acct": [_purchase("acct-1", "CARD BILL"), _purchase("acct-2", "CARD BILL", "9.90")],
        "card": ProviderRateLimited("slow down"),
    })
    await _sync(session, conn, test_workspace.id, test_user.id, second)

    purchases = list((await session.execute(
        select(Transaction).where(Transaction.external_id.in_(["card-1", "card-2"]))
    )).scalars())
    assert len(purchases) == 2
    assert not any(tx.is_ignored for tx in purchases)
