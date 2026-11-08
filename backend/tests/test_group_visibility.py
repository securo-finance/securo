"""Cross-user visibility for groups: an owner can see/edit, a linked
member can see, others see nothing. Settlement and balance reads work
for linked members; writes are owner-only."""

import uuid
from datetime import date
from decimal import Decimal

import bcrypt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.services import (
    balance_service,
    group_service,
    settlement_service,
    split_service,
    workspace_service,
)


async def _make_user(session: AsyncSession, email: str) -> User:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_user_with_workspace(
    session: AsyncSession, email: str
) -> tuple[User, Workspace]:
    user = await _make_user(session, email)
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


@pytest.mark.asyncio
async def test_email_auto_resolves_to_linked_user(
    session: AsyncSession, test_user, test_workspace
):
    other = await _make_user(session, "friend@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    member = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Friend", email="friend@example.com"),
    )

    assert member is not None
    assert member.linked_user_id == other.id


@pytest.mark.asyncio
async def test_email_unknown_creates_shadow(
    session: AsyncSession, test_user, test_workspace
):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    member = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Stranger", email="nobody@example.org"),
    )

    assert member is not None
    assert member.linked_user_id is None
    assert member.email == "nobody@example.org"


@pytest.mark.asyncio
async def test_linked_member_sees_group_in_list(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Viewer", email="viewer@example.com"),
    )

    visible = await group_service.list_groups(session, other_ws.id, other.id)
    assert {g.id for g in visible} == {group.id}
    # Linked member sees is_owner=False; the original owner sees True.
    assert visible[0].is_owner is False

    searched = await group_service.list_groups(
        session, other_ws.id, other.id, q="SHARED", status="all"
    )
    assert {g.id for g in searched} == {group.id}

    owners_view = await group_service.list_groups(
        session, test_workspace.id, test_user.id
    )
    assert owners_view[0].is_owner is True


@pytest.mark.asyncio
async def test_unrelated_user_does_not_see_group(
    session: AsyncSession, test_user, test_workspace
):
    stranger, stranger_ws = await _make_user_with_workspace(
        session, "stranger@example.com"
    )
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Private")
    )

    visible = await group_service.list_groups(session, stranger_ws.id, stranger.id)
    assert visible == []

    searched = await group_service.list_groups(
        session, stranger_ws.id, stranger.id, q="Private", status="all"
    )
    assert searched == []


@pytest.mark.asyncio
async def test_visible_get_works_for_linked_member(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    via_visible = await group_service.get_group_visible(
        session, group.id, other_ws.id, other.id
    )
    assert via_visible is not None
    assert via_visible.is_owner is False

    # Owner-only get returns None when scoped to the linked member's
    # workspace — edit paths reject non-owners cleanly.
    via_owned = await group_service.get_group(session, group.id, other_ws.id)
    assert via_owned is None


@pytest.mark.asyncio
async def test_linked_member_cannot_modify_group(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    # Updates from the linked member's own workspace resolve to None (no-op).
    from app.schemas.group import GroupUpdate

    updated = await group_service.update_group(
        session, group.id, other_ws.id, other.id, GroupUpdate(name="Hijacked")
    )
    assert updated is None

    # Member-add scoped to the linked viewer's workspace is also rejected.
    add = await group_service.create_member(
        session, group.id, other_ws.id, GroupMemberCreate(name="Bob")
    )
    assert add is None


@pytest.mark.asyncio
async def test_linked_member_can_read_balances_and_settlements(
    session: AsyncSession, test_user, test_workspace
):
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="V", email="viewer@example.com"),
    )

    balances = await balance_service.compute_balances(
        session, group.id, other_ws.id, other.id
    )
    assert balances is not None
    assert balances["lines"] == []

    settlements = await settlement_service.list_settlements(
        session, group.id, other_ws.id, other.id
    )
    assert settlements == []


@pytest.mark.asyncio
async def test_linked_member_can_settle_own_debt(
    session: AsyncSession, test_user, test_workspace
):
    """A linked member CAN record a settlement when they are the
    from_member (i.e., they're recording a payment they made)."""
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    self_m = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Me", is_self=True)
    )
    a = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="A", email="viewer@example.com"),
    )

    assert self_m is not None
    assert a is not None

    settlement = await settlement_service.create_settlement(
        session,
        group.id,
        other_ws.id,
        other.id,
        GroupSettlementCreate(
            from_member_id=a.id,  # the linked member themself
            to_member_id=self_m.id,
            amount=Decimal("5.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert settlement is not None
    assert settlement.from_member_id == a.id


@pytest.mark.asyncio
async def test_linked_member_cannot_settle_someone_elses_debt(
    session: AsyncSession, test_user, test_workspace
):
    """A linked member CANNOT speak for another member."""
    other, other_ws = await _make_user_with_workspace(session, "viewer@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    a = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="A", email="viewer@example.com"),
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="B")
    )

    assert a is not None
    assert b is not None

    with pytest.raises(PermissionError):
        await settlement_service.create_settlement(
            session,
            group.id,
            other_ws.id,
            other.id,
            GroupSettlementCreate(
                from_member_id=b.id,  # NOT the linked member's own id
                to_member_id=a.id,
                amount=Decimal("5.00"),
                currency="USD",
                date=date.today(),
            ),
        )


async def _record_split_expense(session, user, workspace, members, amount):
    """`user` pays `amount` from an account in `workspace`, split equally."""
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Wallet",
        type="checking", balance=Decimal("0"), currency="USD",
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=account.id,
        description="Dinner", amount=Decimal(amount), currency="USD", date=date.today(),
        type="debit", source="manual",
    )
    session.add(tx)
    await session.flush()
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        user.id,
    )
    await session.commit()


async def _ledger(session, group_id, workspace_id, user_id):
    balances = await balance_service.compute_balances(session, group_id, workspace_id, user_id)
    assert balances is not None
    return {ln["member_id"]: ln["amount"] for ln in balances["lines"]}


@pytest.mark.asyncio
async def test_balances_follow_the_member_who_paid(
    session: AsyncSession, test_user, test_workspace
):
    """Issue #1062: B, linked from their own workspace, pays two split
    expenses. A owes B 15.00 in both views; before, each saw the other as
    the debtor."""
    other, other_ws = await _make_user_with_workspace(session, "b@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Flat")
    )
    a = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A", email=test_user.email)
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B", email="b@example.com")
    )
    assert a is not None and b is not None
    assert a.linked_user_id == test_user.id and b.linked_user_id == other.id

    await _record_split_expense(session, other, other_ws, [a, b], "10.00")
    await _record_split_expense(session, other, other_ws, [a, b], "20.00")

    # B's view: A owes B.
    assert await _ledger(session, group.id, other_ws.id, other.id) == {a.id: Decimal("15.00")}
    # A's view: A owes B (negative), not the mirror image.
    assert await _ledger(session, group.id, test_workspace.id, test_user.id) == {
        b.id: Decimal("-15.00")
    }


@pytest.mark.asyncio
async def test_balances_net_expenses_paid_by_both_sides(
    session: AsyncSession, test_user, test_workspace
):
    """The owner's own member is the stored, unlinked `is_self` row. The
    owner pays 40.00 and B pays 10.00, both split equally: B owes 15.00
    net, seen the same way from both sides, and a settlement from B to the
    owner reduces it for both."""
    other, other_ws = await _make_user_with_workspace(session, "b@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    me = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Me", is_self=True)
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B", email="b@example.com")
    )
    assert me is not None and b is not None

    await _record_split_expense(session, test_user, test_workspace, [me, b], "40.00")
    await _record_split_expense(session, other, other_ws, [me, b], "10.00")

    assert await _ledger(session, group.id, test_workspace.id, test_user.id) == {
        b.id: Decimal("15.00")
    }
    assert await _ledger(session, group.id, other_ws.id, other.id) == {me.id: Decimal("-15.00")}

    await settlement_service.create_settlement(
        session,
        group.id,
        other_ws.id,
        other.id,
        GroupSettlementCreate(
            from_member_id=b.id, to_member_id=me.id, amount=Decimal("5.00"),
            currency="USD", date=date.today(),
        ),
    )
    await session.commit()

    assert await _ledger(session, group.id, test_workspace.id, test_user.id) == {
        b.id: Decimal("10.00")
    }
    assert await _ledger(session, group.id, other_ws.id, other.id) == {me.id: Decimal("-10.00")}


@pytest.mark.asyncio
async def test_owner_without_member_row_is_not_shown_to_members(
    session: AsyncSession, test_user, test_workspace
):
    """The owner isn't a member of the group and pays 30.00 split between B
    and C; B then pays 20.00 split the same way. The owner sees both debts
    to them. B sees only C's debt: the owner has no member row, so there is
    no name or id to show B's debt to them under."""
    other, other_ws = await _make_user_with_workspace(session, "b@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    b = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B", email="b@example.com")
    )
    c = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="C")
    )
    assert b is not None and c is not None

    await _record_split_expense(session, test_user, test_workspace, [b, c], "30.00")
    await _record_split_expense(session, other, other_ws, [b, c], "20.00")

    assert await _ledger(session, group.id, test_workspace.id, test_user.id) == {
        b.id: Decimal("15.00"),
        c.id: Decimal("15.00"),
    }
    b_view = await balance_service.compute_balances(session, group.id, other_ws.id, other.id)
    assert b_view is not None
    assert b_view["self_member_id"] == b.id
    assert {ln["member_id"]: ln["amount"] for ln in b_view["lines"]} == {c.id: Decimal("10.00")}
