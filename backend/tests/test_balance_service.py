import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate
from app.schemas.transaction_split import (
    TransactionSplitInput,
    TransactionSplitsInput,
)
from app.services import (
    balance_service,
    group_service,
    settlement_service,
    split_service,
)


async def _make_account(session: AsyncSession, user_id) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name="W",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    return account


async def _make_tx(session, user_id, account_id, amount, currency="USD") -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description="t",
        amount=Decimal(amount),
        currency=currency,
        date=date.today(),
        type="debit",
        source="manual",
    )
    session.add(tx)
    await session.flush()
    return tx


async def _setup(session, test_user, workspace_id, n_others=2):
    group = await group_service.create_group(
        session, workspace_id, test_user.id, GroupCreate(name="Trip")
    )
    self_member = await group_service.create_member(
        session,
        group.id,
        workspace_id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    others = []
    for i in range(n_others):
        m = await group_service.create_member(
            session,
            group.id,
            workspace_id,
            GroupMemberCreate(name=f"Friend{i}"),
        )
        others.append(m)
    return group, self_member, others


@pytest.mark.asyncio
async def test_balances_each_friend_owes_their_share(
    session: AsyncSession, test_user, test_workspace
):
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=3
    )
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "120.00")

    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[
            TransactionSplitInput(group_member_id=self_m.id),
            *[TransactionSplitInput(group_member_id=f.id) for f in friends],
        ],
    )
    await split_service.replace_splits(session, tx, payload, test_user.id)
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_member = {ln["member_id"]: ln["amount"] for ln in balances["lines"]}
    # Each friend owes 30.00, self balance line excluded.
    for f in friends:
        assert by_member[f.id] == Decimal("30.00")
    assert self_m.id not in by_member


@pytest.mark.asyncio
async def test_settlement_reduces_balance(
    session: AsyncSession, test_user, test_workspace
):
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=2
    )
    friend = friends[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "100.00")

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=self_m.id, share_amount=Decimal("60.00")
                ),
                TransactionSplitInput(
                    group_member_id=friend.id, share_amount=Decimal("40.00")
                ),
            ],
        ),
        test_user.id,
    )
    # Friend pays back 25 of the 40 they owe.
    await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=friend.id,
            to_member_id=self_m.id,
            amount=Decimal("25.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_member = {ln["member_id"]: ln["amount"] for ln in balances["lines"]}
    assert by_member[friend.id] == Decimal("15.00")


@pytest.mark.asyncio
async def test_self_loan_increases_balance_owed(
    session: AsyncSession, test_user, test_workspace
):
    """Self lending money to a friend should make them owe more, even
    without an associated split (e.g. a direct cash advance)."""
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=1
    )
    friend = friends[0]

    await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=self_m.id,
            to_member_id=friend.id,
            amount=Decimal("50.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_member = {ln["member_id"]: ln["amount"] for ln in balances["lines"]}
    assert by_member[friend.id] == Decimal("50.00")


@pytest.mark.asyncio
async def test_balances_segregated_by_currency(
    session: AsyncSession, test_user, test_workspace
):
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=1
    )
    friend = friends[0]
    account = await _make_account(session, test_user.id)

    tx_usd = await _make_tx(session, test_user.id, account.id, "60.00", currency="USD")
    tx_eur = await _make_tx(session, test_user.id, account.id, "40.00", currency="EUR")

    for tx in (tx_usd, tx_eur):
        await split_service.replace_splits(
            session,
            tx,
            TransactionSplitsInput(
                share_type="equal",
                splits=[
                    TransactionSplitInput(group_member_id=self_m.id),
                    TransactionSplitInput(group_member_id=friend.id),
                ],
            ),
            test_user.id,
        )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_currency = {
        ln["currency"]: ln["amount"]
        for ln in balances["lines"]
        if ln["member_id"] == friend.id
    }
    assert by_currency["USD"] == Decimal("30.00")
    assert by_currency["EUR"] == Decimal("20.00")


@pytest.mark.asyncio
async def test_balance_line_dropped_when_settlement_zeroes_out_split(
    session: AsyncSession, test_user, test_workspace
):
    """A friend who pays back exactly their share should drop off the
    balance ledger — no zero-amount line should be returned."""
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=1
    )
    friend = friends[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "40.00")

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[
                TransactionSplitInput(group_member_id=self_m.id),
                TransactionSplitInput(group_member_id=friend.id),
            ],
        ),
        test_user.id,
    )
    # Friend owes 20; pays back exactly 20.
    await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=friend.id,
            to_member_id=self_m.id,
            amount=Decimal("20.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    friend_lines = [ln for ln in balances["lines"] if ln["member_id"] == friend.id]
    assert friend_lines == []


@pytest.mark.asyncio
async def test_cross_member_settlements_are_ignored_in_owner_ledger(
    session: AsyncSession, test_user, test_workspace
):
    """Settlements between two non-self members don't affect the
    owner-ledger view (balance_service is owner-centric in v1)."""
    group, self_m, friends = await _setup(
        session, test_user, test_workspace.id, n_others=2
    )
    a, b = friends
    account = await _make_account(session, test_user.id)

    # Owner pays $30 split equally with A — A owes $15.
    tx = await _make_tx(session, test_user.id, account.id, "30.00")
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[
                TransactionSplitInput(group_member_id=self_m.id),
                TransactionSplitInput(group_member_id=a.id),
            ],
        ),
        test_user.id,
    )
    # A "settles" with B — neither side is self. Should be a no-op for
    # the owner's balance ledger.
    await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=a.id,
            to_member_id=b.id,
            amount=Decimal("100.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_member = {ln["member_id"]: ln["amount"] for ln in balances["lines"]}
    # A still owes their original $15 — not adjusted by the cross-member tx.
    assert by_member[a.id] == Decimal("15.00")
    # B has no obligation to the owner — no line.
    assert b.id not in by_member


@pytest.mark.asyncio
async def test_no_self_member_means_only_split_totals(
    session: AsyncSession, test_user, test_workspace
):
    """Without a self member, settlements can't be applied (cross-member
    settlements are out of scope for v1) — only split shares accumulate."""
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="NoSelf")
    )
    a = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A")
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B")
    )
    assert a is not None
    assert b is not None
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "20.00")
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[
                TransactionSplitInput(group_member_id=a.id),
                TransactionSplitInput(group_member_id=b.id),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, group.id, test_workspace.id, test_user.id
    )
    assert balances is not None
    by_member = {ln["member_id"]: ln["amount"] for ln in balances["lines"]}
    assert by_member[a.id] == Decimal("10.00")
    assert by_member[b.id] == Decimal("10.00")
