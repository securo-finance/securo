"""Coverage-focused tests for group_service.

Targets update_group name-clash, delete IntegrityError translation,
update_member (email re-resolve + name clash + self promotion), delete_member,
get_group_visible cross-workspace projection, and list_transactions.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import bcrypt as _bcrypt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.schemas.group import (
    GroupCreate,
    GroupMemberCreate,
    GroupMemberUpdate,
    GroupUpdate,
)
from app.services import group_service, workspace_service


async def _make_user_with_workspace(session, email):
    hashed = _bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_verified=True,
        preferences={"currency_display": "USD"},
    )
    session.add(user)
    await session.flush()
    ws = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, ws


@pytest.mark.asyncio
async def test_update_group_name_clash(session: AsyncSession, test_user, test_workspace):
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Alpha")
    )
    g2 = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Beta")
    )
    with pytest.raises(ValueError, match="already exists"):
        await group_service.update_group(
            session, g2.id, test_workspace.id, test_user.id,
            GroupUpdate(name="Alpha"),
        )


@pytest.mark.asyncio
async def test_update_group_not_found(session: AsyncSession, test_user, test_workspace):
    result = await group_service.update_group(
        session, uuid.uuid4(), test_workspace.id, test_user.id,
        GroupUpdate(name="X"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_group_integrity_error_translated(session: AsyncSession, test_user, test_workspace, monkeypatch):
    """A FK RESTRICT violation (member with active splits) surfaces as a
    friendly ValueError. SQLite doesn't enforce RESTRICT under test, so we
    simulate the commit raising IntegrityError to cover the translation."""
    from sqlalchemy.exc import IntegrityError

    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="HasSplits")
    )

    async def _boom():
        raise IntegrityError("DELETE", {}, Exception("FK RESTRICT"))

    monkeypatch.setattr(session, "commit", _boom)

    with pytest.raises(ValueError, match="referenced by transaction splits"):
        await group_service.delete_group(session, group.id, test_workspace.id)


@pytest.mark.asyncio
async def test_delete_group_not_found(session: AsyncSession, test_user, test_workspace):
    assert await group_service.delete_group(session, uuid.uuid4(), test_workspace.id) is False


@pytest.mark.asyncio
async def test_update_member_resolves_email_link(session: AsyncSession, test_user, test_workspace):
    other, _ws = await _make_user_with_workspace(session, "linkme@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    m = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Friend")
    )

    assert m is not None
    assert m.linked_user_id is None

    updated = await group_service.update_member(
        session, group.id, m.id, test_workspace.id,
        GroupMemberUpdate(email="linkme@example.com"),
    )
    assert updated is not None
    assert updated.linked_user_id == other.id


@pytest.mark.asyncio
async def test_update_member_name_clash(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Alice")
    )
    bob = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Bob")
    )

    assert bob is not None
    with pytest.raises(ValueError, match="already exists"):
        await group_service.update_member(
            session, group.id, bob.id, test_workspace.id,
            GroupMemberUpdate(name="Alice"),
        )


@pytest.mark.asyncio
async def test_update_member_group_not_found(session: AsyncSession, test_user, test_workspace):
    result = await group_service.update_member(
        session, uuid.uuid4(), uuid.uuid4(), test_workspace.id,
        GroupMemberUpdate(name="X"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_update_member_member_not_found(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    result = await group_service.update_member(
        session, group.id, uuid.uuid4(), test_workspace.id,
        GroupMemberUpdate(name="X"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_member(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    m = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Temp")
    )

    assert m is not None
    assert await group_service.delete_member(session, group.id, m.id, test_workspace.id) is True
    # Now gone.
    assert await group_service.delete_member(session, group.id, m.id, test_workspace.id) is False


@pytest.mark.asyncio
async def test_delete_member_group_not_found(session: AsyncSession, test_user, test_workspace):
    assert await group_service.delete_member(
        session, uuid.uuid4(), uuid.uuid4(), test_workspace.id
    ) is False


@pytest.mark.asyncio
async def test_delete_member_integrity_error_translated(session: AsyncSession, test_user, test_workspace, monkeypatch):
    """FK RESTRICT on a member with active splits surfaces as a friendly
    ValueError. Simulated via a commit that raises IntegrityError because
    SQLite doesn't enforce RESTRICT under test."""
    from sqlalchemy.exc import IntegrityError

    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="G")
    )
    member = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Alice")
    )

    assert member is not None

    async def _boom():
        raise IntegrityError("DELETE", {}, Exception("FK RESTRICT"))

    monkeypatch.setattr(session, "commit", _boom)

    with pytest.raises(ValueError, match="referenced by transaction splits"):
        await group_service.delete_member(session, group.id, member.id, test_workspace.id)


@pytest.mark.asyncio
async def test_get_group_visible_cross_workspace(session: AsyncSession, test_user, test_workspace):
    """A user linked as a non-self member from another workspace sees the group."""
    other, other_ws = await _make_user_with_workspace(session, "cross@example.com")
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Shared")
    )
    await group_service.create_member(
        session, group.id, test_workspace.id,
        GroupMemberCreate(name="Cross", linked_user_id=other.id),
    )

    # The cross-workspace user sees it through their own workspace scope.
    visible = await group_service.get_group_visible(
        session, group.id, other_ws.id, other.id
    )
    assert visible is not None
    assert visible.is_owner is False

    # And via list_groups too.
    listed = await group_service.list_groups(session, other_ws.id, other.id)
    assert group.id in {g.id for g in listed}


@pytest.mark.asyncio
async def test_list_transactions(session: AsyncSession, test_user, test_workspace, test_account):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="WithTx")
    )
    member = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Alice")
    )
    assert member is not None
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Group dinner",
        amount=Decimal("40.00"),
        date=date.today(),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    session.add(TransactionSplit(
        id=uuid.uuid4(),
        transaction_id=tx.id,
        workspace_id=test_workspace.id,
        group_member_id=member.id,
        share_type="equal",
        share_amount=Decimal("40.00"),
    ))
    await session.commit()

    txs = await group_service.list_transactions(
        session, group.id, test_workspace.id, test_user.id
    )
    assert txs is not None
    assert len(txs) == 1
    assert txs[0].description == "Group dinner"
    assert txs[0].attachment_count == 0


@pytest.mark.asyncio
async def test_list_transactions_group_not_visible(session: AsyncSession, test_user, test_workspace):
    result = await group_service.list_transactions(
        session, uuid.uuid4(), test_workspace.id, test_user.id
    )
    assert result is None


@pytest.mark.asyncio
async def test_create_group_duplicate_name_rejected(session: AsyncSession, test_user, test_workspace):
    await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Dup")
    )
    with pytest.raises(ValueError, match="already exists"):
        await group_service.create_group(
            session, test_workspace.id, test_user.id, GroupCreate(name="dup")
        )


@pytest.mark.asyncio
async def test_update_group_changes_fields(session: AsyncSession, test_user, test_workspace):
    g = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Editable")
    )
    updated = await group_service.update_group(
        session, g.id, test_workspace.id, test_user.id,
        GroupUpdate(name="Renamed", notes="hi", color="#abcdef"),
    )
    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.notes == "hi"
    assert updated.color == "#abcdef"


@pytest.mark.asyncio
async def test_delete_group_success(session: AsyncSession, test_user, test_workspace):
    g = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Gone")
    )
    assert await group_service.delete_group(session, g.id, test_workspace.id) is True


@pytest.mark.asyncio
async def test_list_members_group_not_visible(session: AsyncSession, test_user, test_workspace):
    assert await group_service.list_members(
        session, uuid.uuid4(), test_workspace.id, test_user.id
    ) is None


@pytest.mark.asyncio
async def test_create_member_group_not_found(session: AsyncSession, test_user, test_workspace):
    assert await group_service.create_member(
        session, uuid.uuid4(), test_workspace.id, GroupMemberCreate(name="X")
    ) is None


@pytest.mark.asyncio
async def test_create_member_name_clash(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="ClashG")
    )
    await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="Alice")
    )
    with pytest.raises(ValueError, match="already exists"):
        await group_service.create_member(
            session, group.id, test_workspace.id, GroupMemberCreate(name="alice")
        )


@pytest.mark.asyncio
async def test_create_member_self_demotes_prior_self(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="SelfDemote")
    )
    m1 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A", is_self=True)
    )
    m2 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B", is_self=True)
    )
    members = await group_service.list_members(
        session, group.id, test_workspace.id, test_user.id
    )

    assert m1 is not None
    assert m2 is not None
    assert members is not None
    by_id = {m.id: m for m in members}
    assert by_id[m1.id].is_self is False
    assert by_id[m2.id].is_self is True


@pytest.mark.asyncio
async def test_update_member_promote_self_demotes_existing(session: AsyncSession, test_user, test_workspace):
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="SelfSwap")
    )
    m1 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A", is_self=True)
    )
    m2 = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B")
    )

    assert m1 is not None
    assert m2 is not None
    await group_service.update_member(
        session, group.id, m2.id, test_workspace.id, GroupMemberUpdate(is_self=True)
    )
    members = await group_service.list_members(
        session, group.id, test_workspace.id, test_user.id
    )
    assert members is not None
    by_id = {m.id: m for m in members}
    assert by_id[m1.id].is_self is False
    assert by_id[m2.id].is_self is True
