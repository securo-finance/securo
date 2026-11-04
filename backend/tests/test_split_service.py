import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.transaction_split import (
    TransactionSplitInput,
    TransactionSplitsInput,
)
from app.services import group_service, split_service


async def _make_account(session: AsyncSession, user_id) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Wallet",
        type="checking",
        balance=Decimal("1000.00"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    return account


async def _make_tx(session: AsyncSession, user_id, account_id, amount: Decimal) -> Transaction:
    from datetime import date

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description="Test",
        amount=amount,
        currency="USD",
        date=date.today(),
        type="debit",
        source="manual",
    )
    session.add(tx)
    await session.flush()
    return tx


async def _make_group_with_members(session, user_id, workspace_id, names):
    group = await group_service.create_group(
        session, workspace_id, user_id, GroupCreate(name=f"G-{uuid.uuid4().hex[:6]}")
    )
    members = []
    for n in names:
        m = await group_service.create_member(
            session, group.id, workspace_id, GroupMemberCreate(name=n)
        )
        members.append(m)
    return group, members


async def _read_splits(session, transaction_id):
    rows = (
        await session.execute(
            select(TransactionSplit).where(
                TransactionSplit.transaction_id == transaction_id
            )
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_equal_split_three_ways_assigns_residual_to_last(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B", "C"]
    )

    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
    )
    await split_service.replace_splits(session, tx, payload, test_user.id)

    splits = await _read_splits(session, tx.id)
    by_member = {s.group_member_id: s.share_amount for s in splits}
    # 100 / 3 = 33.33 each, last share absorbs the residual to make 33.34
    assert by_member[members[0].id] == Decimal("33.33")
    assert by_member[members[1].id] == Decimal("33.33")
    assert by_member[members[2].id] == Decimal("33.34")
    assert sum(by_member.values()) == Decimal("100.00")


@pytest.mark.asyncio
async def test_percent_split_residual_on_last(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B", "C"]
    )

    payload = TransactionSplitsInput(
        share_type="percent",
        splits=[
            TransactionSplitInput(group_member_id=members[0].id, share_pct=Decimal("33.33")),
            TransactionSplitInput(group_member_id=members[1].id, share_pct=Decimal("33.33")),
            TransactionSplitInput(group_member_id=members[2].id, share_pct=Decimal("33.34")),
        ],
    )
    await split_service.replace_splits(session, tx, payload, test_user.id)

    splits = await _read_splits(session, tx.id)
    total = sum(s.share_amount for s in splits)
    assert total == Decimal("100.00")
    # share_pct preserved per row
    by_member = {s.group_member_id: s for s in splits}
    assert by_member[members[0].id].share_pct == Decimal("33.33")
    assert by_member[members[2].id].share_pct == Decimal("33.34")


@pytest.mark.asyncio
async def test_percent_must_sum_to_100(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B"]
    )

    payload = TransactionSplitsInput(
        share_type="percent",
        splits=[
            TransactionSplitInput(group_member_id=members[0].id, share_pct=Decimal("60")),
            TransactionSplitInput(group_member_id=members[1].id, share_pct=Decimal("30")),
        ],
    )
    with pytest.raises(ValueError, match="sum to 100"):
        await split_service.replace_splits(session, tx, payload, test_user.id)


@pytest.mark.asyncio
async def test_exact_amounts_must_sum_to_total(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("100.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B"]
    )

    payload = TransactionSplitsInput(
        share_type="exact",
        splits=[
            TransactionSplitInput(
                group_member_id=members[0].id, share_amount=Decimal("60.00")
            ),
            TransactionSplitInput(
                group_member_id=members[1].id, share_amount=Decimal("39.00")
            ),
        ],
    )
    with pytest.raises(ValueError, match="sum to the transaction amount"):
        await split_service.replace_splits(session, tx, payload, test_user.id)


@pytest.mark.asyncio
async def test_exact_amounts_credit_transaction_uses_absolute_total(
    session: AsyncSession, test_user, test_workspace
):
    """Income splits work the same as expense splits — sign-agnostic."""
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("200.00"))
    tx.type = "credit"
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B"]
    )

    payload = TransactionSplitsInput(
        share_type="exact",
        splits=[
            TransactionSplitInput(
                group_member_id=members[0].id, share_amount=Decimal("120.00")
            ),
            TransactionSplitInput(
                group_member_id=members[1].id, share_amount=Decimal("80.00")
            ),
        ],
    )
    await split_service.replace_splits(session, tx, payload, test_user.id)
    splits = await _read_splits(session, tx.id)
    assert sum(s.share_amount for s in splits) == Decimal("200.00")


@pytest.mark.asyncio
async def test_replace_splits_clears_previous(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("60.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A", "B", "C"]
    )

    # First, equal split across 3
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )
    assert len(await _read_splits(session, tx.id)) == 3

    # Then replace with 2-way exact
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=members[0].id, share_amount=Decimal("20.00")
                ),
                TransactionSplitInput(
                    group_member_id=members[1].id, share_amount=Decimal("40.00")
                ),
            ],
        ),
        test_user.id,
    )
    splits = await _read_splits(session, tx.id)
    assert len(splits) == 2
    assert sum(s.share_amount for s in splits) == Decimal("60.00")


@pytest.mark.asyncio
async def test_members_must_share_one_group(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("50.00"))
    _, members_a = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A"]
    )
    _, members_b = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["B"]
    )

    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[
            TransactionSplitInput(group_member_id=members_a[0].id),
            TransactionSplitInput(group_member_id=members_b[0].id),
        ],
    )
    with pytest.raises(ValueError, match="same group"):
        await split_service.replace_splits(session, tx, payload, test_user.id)


@pytest.mark.asyncio
async def test_members_must_belong_to_owner(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("50.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A"]
    )

    other_user = uuid.uuid4()
    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[TransactionSplitInput(group_member_id=members[0].id)],
    )
    with pytest.raises(ValueError, match="not found"):
        await split_service.replace_splits(session, tx, payload, other_user)


async def _user_with_workspace(session: AsyncSession, email: str):
    import bcrypt

    from app.models.user import User
    from app.services import workspace_service

    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


async def _make_workspace_tx(session: AsyncSession, user, workspace) -> Transaction:
    from datetime import date

    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Wallet",
        type="checking", balance=Decimal("100.00"), currency="USD",
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=account.id,
        description="Dinner", amount=Decimal("40.00"), currency="USD", date=date.today(),
        type="debit", source="manual",
    )
    session.add(tx)
    await session.flush()
    return tx


@pytest.mark.asyncio
async def test_member_linked_from_another_workspace_can_split(session: AsyncSession):
    """Regression for #972: a member linked into a group from their own
    workspace can save a split on it, as the group read paths allow."""
    suffix = uuid.uuid4().hex[:8]
    owner, owner_ws = await _user_with_workspace(session, f"owner-{suffix}@example.com")
    friend, friend_ws = await _user_with_workspace(session, f"friend-{suffix}@example.com")

    group = await group_service.create_group(
        session, owner_ws.id, owner.id, GroupCreate(name="Trip")
    )
    owner_member = await group_service.create_member(
        session, group.id, owner_ws.id, GroupMemberCreate(name="Owner")
    )
    friend_member = await group_service.create_member(
        session, group.id, owner_ws.id,
        GroupMemberCreate(name="Friend", email=f"friend-{suffix}@example.com"),
    )
    assert owner_member is not None
    assert friend_member is not None
    assert friend_member.linked_user_id == friend.id

    tx = await _make_workspace_tx(session, friend, friend_ws)
    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[
            TransactionSplitInput(group_member_id=owner_member.id),
            TransactionSplitInput(group_member_id=friend_member.id),
        ],
    )
    await split_service.replace_splits(session, tx, payload, friend.id)

    splits = await _read_splits(session, tx.id)
    assert {s.group_member_id for s in splits} == {owner_member.id, friend_member.id}


@pytest.mark.asyncio
async def test_unlinked_group_in_another_workspace_is_rejected(session: AsyncSession):
    """The #375 hardening still holds: a group from another workspace the
    user is not linked to cannot be referenced."""
    suffix = uuid.uuid4().hex[:8]
    owner, owner_ws = await _user_with_workspace(session, f"owner-{suffix}@example.com")
    stranger, stranger_ws = await _user_with_workspace(
        session, f"stranger-{suffix}@example.com"
    )
    _, members = await _make_group_with_members(session, owner.id, owner_ws.id, ["A"])

    tx = await _make_workspace_tx(session, stranger, stranger_ws)
    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[TransactionSplitInput(group_member_id=members[0].id)],
    )
    with pytest.raises(ValueError, match="not found"):
        await split_service.replace_splits(session, tx, payload, stranger.id)


@pytest.mark.asyncio
async def test_no_duplicate_member_per_transaction(
    session: AsyncSession, test_user, test_workspace
):
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, Decimal("50.00"))
    _, members = await _make_group_with_members(
        session, test_user.id, test_workspace.id, ["A"]
    )

    payload = TransactionSplitsInput(
        share_type="equal",
        splits=[
            TransactionSplitInput(group_member_id=members[0].id),
            TransactionSplitInput(group_member_id=members[0].id),
        ],
    )
    with pytest.raises(ValueError, match="at most once"):
        await split_service.replace_splits(session, tx, payload, test_user.id)
