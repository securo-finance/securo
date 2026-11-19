"""Issue #1305: the cross-workspace share projection must be symmetric.

An invited member already sees both ends of a split expense: their own
parent row and the owner's. The group owner must see the invited member's
split in the default statement scope too, without ever getting their own
transactions re-projected as "shared" after switching workspaces.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.services import dashboard_service, group_service, split_service, workspace_service
from app.services._query_filters import viewer_shared_pnl
from app.services.search_service import search_all
from app.services.transaction_service import get_transactions


async def _make_user_with_workspace(
    session: AsyncSession, email: str
) -> tuple[User, "object"]:
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
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


async def _record_split_expense(
    session, payer, workspace, members, amount: str, description: str
) -> Transaction:
    """`payer` pays `amount` from an account in `workspace`, split equally."""
    account = Account(
        id=uuid.uuid4(),
        user_id=payer.id,
        workspace_id=workspace.id,
        name="Wallet",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=payer.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        date=date.today(),
        type="debit",
        source="manual",
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
        payer.id,
    )
    await session.commit()
    return tx


async def _trip_group(session, test_user, test_workspace, *, linked_self: bool):
    """A group owned by `test_user` with their self-member plus an invited
    member linked to a fresh user in another workspace."""
    guest, guest_ws = await _make_user_with_workspace(
        session, f"guest-{uuid.uuid4().hex[:8]}@example.com"
    )
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    me = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(
            name="Me",
            is_self=True,
            email=test_user.email if linked_self else None,
        ),
    )
    friend = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Guest", email=guest.email),
    )
    assert me is not None and friend is not None
    assert friend.linked_user_id == guest.id
    assert (me.linked_user_id == test_user.id) is linked_self
    return group, me, friend, guest, guest_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_self", [False, True])
async def test_owner_sees_invited_members_shared_expense(
    session: AsyncSession, test_user, test_workspace, linked_self
):
    """The invited member's expense lands in the owner's default list as a
    shared row carrying the owner's share, the case that was asymmetric."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=linked_self
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    rows, _, _ = await get_transactions(session, test_workspace.id, test_user.id)

    shared = [row for row in rows if row.description == "Groceries"]
    assert len(shared) == 1
    row = shared[0]
    assert row.is_shared is True
    assert row.viewer_share == Decimal("50.00")
    assert row.group_id == group.id
    # The expense's author is the invited member, not the group owner.
    assert row.parent_owner_name == "Guest"

    hits = await search_all(session, test_workspace.id, test_user.id, "Groceries")
    assert any(
        hit["type"] == "transaction" and hit["label"] == "Groceries" for hit in hits
    ), hits


@pytest.mark.asyncio
async def test_owner_own_split_expense_is_not_reprojected_in_another_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """The owner keeps seeing their own expense once, where it lives. The
    dedup guard is the row's author, not the `is_self` flag of their member."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )
    await _record_split_expense(
        session, test_user, test_workspace, [me, friend], "40.00", "Owner dinner"
    )

    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )

    rows, _, _ = await get_transactions(session, other_ws.id, test_user.id)
    descriptions = [row.description for row in rows]
    assert "Owner dinner" not in descriptions
    groceries = [row for row in rows if row.description == "Groceries"]
    assert len(groceries) == 1
    assert groceries[0].is_shared is True


@pytest.mark.asyncio
async def test_owner_does_not_see_a_workspace_row_as_shared(
    session: AsyncSession, test_user, test_workspace
):
    """A linked self-member must not turn a coworker's row in the owner's
    own workspace into a locked "shared" row: only the other workspace's
    share is projected to the owner."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=True
    )
    await _record_split_expense(
        session, guest, test_workspace, [me, friend], "60.00", "Team lunch"
    )

    rows, _, _ = await get_transactions(session, test_workspace.id, test_user.id)
    row = next(r for r in rows if r.description == "Team lunch")
    assert row.is_shared is False


@pytest.mark.asyncio
async def test_owner_group_stays_reachable_from_their_other_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """The projected row links to its group; the owner must be able to open
    the group from any workspace they belong to, like an invited member."""
    group, _me, _friend, _guest, _guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )

    listed = await group_service.list_groups(session, other_ws.id, test_user.id)
    assert group.id in {g.id for g in listed}
    visible = await group_service.get_group_visible(
        session, group.id, other_ws.id, test_user.id
    )
    assert visible is not None


@pytest.mark.asyncio
async def test_owner_cannot_edit_or_delete_the_invited_members_row(
    session: AsyncSession,
    client: AsyncClient,
    auth_headers,
    test_user,
    test_workspace,
):
    """The projected row is read-only for the owner. The API resolves a
    transaction inside the workspace that owns it, so edit and delete from
    a workspace where the row only shows up as a share answer 404."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    guest_tx = await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )
    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )
    headers = {**auth_headers, "X-Workspace-Id": str(other_ws.id)}

    rows, _, _ = await get_transactions(session, other_ws.id, test_user.id)
    assert any(row.id == guest_tx.id and row.is_shared for row in rows)

    patched = await client.patch(
        f"/api/transactions/{guest_tx.id}",
        json={"description": "Mine now"},
        headers=headers,
    )
    assert patched.status_code == 404, patched.text

    deleted = await client.delete(
        f"/api/transactions/{guest_tx.id}", headers=headers
    )
    assert deleted.status_code == 404, deleted.text


@pytest.mark.asyncio
async def test_reports_count_the_owners_share_of_an_invited_members_expense(
    session: AsyncSession, test_user, test_workspace
):
    """The dashboard/report reader of the same projection must count the
    owner's share of the invited member's expense, not only the invitee's."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    income, expense = await viewer_shared_pnl(
        session, test_user.id, month_start, month_end
    )
    assert income == pytest.approx(0.0)
    assert expense == pytest.approx(50.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_self", [False, True])
async def test_dashboard_counts_a_third_party_row_in_the_owners_workspace_once(
    session: AsyncSession, test_user, test_workspace, linked_self
):
    """A member's expense in the owner's workspace is already in the
    workspace total, whole. Projecting the owner's self-member share of
    that same row would count it twice, so the projection must leave the
    current workspace out while still adding the invited member's row
    from theirs."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=linked_self
    )
    # 100 paid by the invited member inside the owner's workspace, split
    # with the owner's self-member: someone other than the owner paid, but
    # the row already sits in the dashboard base total, whole.
    await _record_split_expense(
        session, guest, test_workspace, [me, friend], "100.00", "Team lunch"
    )
    # 100 paid by the invited member in their own workspace: the owner's
    # 50 share is not in the base total and must still be projected.
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    today = date.today()
    month = today.replace(day=1)

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=month
    )
    assert summary.monthly_expenses == pytest.approx(150.0)

    by_category = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, month=month
    )
    assert sum(item.total for item in by_category) == pytest.approx(150.0)
