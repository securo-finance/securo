import uuid
from datetime import date
from decimal import Decimal

import bcrypt as _bcrypt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import (
    GroupSettlementCreate,
    GroupSettlementUpdate,
)
from app.services import group_service, settlement_service, workspace_service


async def _setup_group(session, user_id, workspace_id):
    group = await group_service.create_group(
        session, workspace_id, user_id, GroupCreate(name=f"S-{uuid.uuid4().hex[:6]}")
    )
    a = await group_service.create_member(
        session, group.id, workspace_id, GroupMemberCreate(name="Alice")
    )
    b = await group_service.create_member(
        session, group.id, workspace_id, GroupMemberCreate(name="Bob")
    )
    return group, a, b


async def _make_user_with_workspace(session, email):
    hashed = _bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"currency_display": "USD"},
    )
    session.add(user)
    await session.flush()
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


async def _make_account(session, user_id, account_type="checking"):
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=f"Acc-{uuid.uuid4().hex[:4]}",
        type=account_type,
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    return account


@pytest.mark.asyncio
async def test_create_settlement_happy_path(
    session: AsyncSession, test_user, test_workspace
):
    group, a, b = await _setup_group(session, test_user.id, test_workspace.id)
    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=a.id,
            to_member_id=b.id,
            amount=Decimal("12.50"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert s is not None
    assert s.amount == Decimal("12.50")
    assert s.currency == "USD"


@pytest.mark.asyncio
async def test_settlement_members_must_belong_to_group(
    session: AsyncSession, test_user, test_workspace
):
    group, a, _b = await _setup_group(session, test_user.id, test_workspace.id)
    other_group, _x, y = await _setup_group(session, test_user.id, test_workspace.id)

    with pytest.raises(ValueError, match="must belong to the group"):
        await settlement_service.create_settlement(
            session,
            group.id,
            test_workspace.id,
            test_user.id,
            GroupSettlementCreate(
                from_member_id=a.id,
                to_member_id=y.id,  # belongs to other_group
                amount=Decimal("5.00"),
                currency="USD",
                date=date.today(),
            ),
        )


@pytest.mark.asyncio
async def test_settlement_from_to_must_differ_at_creation(
    session: AsyncSession, test_user, test_workspace
):
    group, a, _b = await _setup_group(session, test_user.id, test_workspace.id)
    # Pydantic-level validation rejects from == to before service runs.
    with pytest.raises(ValueError, match="must differ"):
        GroupSettlementCreate(
            from_member_id=a.id,
            to_member_id=a.id,
            amount=Decimal("5.00"),
            currency="USD",
            date=date.today(),
        )


@pytest.mark.asyncio
async def test_settlement_update_keeps_invariants(
    session: AsyncSession, test_user, test_workspace
):
    group, a, b = await _setup_group(session, test_user.id, test_workspace.id)
    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=a.id,
            to_member_id=b.id,
            amount=Decimal("10.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert s is not None

    updated = await settlement_service.update_settlement(
        session,
        group.id,
        s.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementUpdate(amount=Decimal("12.00"), notes="adjusted"),
    )

    assert updated is not None
    assert updated.amount == Decimal("12.00")
    assert updated.notes == "adjusted"

    # Updating to set both ends to the same member should fail.
    with pytest.raises(ValueError, match="must differ"):
        await settlement_service.update_settlement(
            session,
            group.id,
            s.id,
            test_workspace.id,
            test_user.id,
            GroupSettlementUpdate(to_member_id=a.id),
        )


@pytest.mark.asyncio
async def test_settlement_owner_isolation(
    session: AsyncSession, test_user, test_workspace
):
    group, a, b = await _setup_group(session, test_user.id, test_workspace.id)
    # A different workspace with a different user — neither can see the
    # group through their own scope.
    other_user, other_ws = await _make_user_with_workspace(
        session, "isolated@example.com"
    )
    result = await settlement_service.create_settlement(
        session,
        group.id,
        other_ws.id,
        other_user.id,
        GroupSettlementCreate(
            from_member_id=a.id,
            to_member_id=b.id,
            amount=Decimal("1.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert result is None


# ---------------------------------------------------------------------------
# receiver_transaction_id (migration 045): when a settlement is recorded,
# the receiver-side credit transaction should be created (and pinned via
# `receiver_transaction_id`) iff the receiver maps to a real Securo user
# AND that user has a checking/savings account to receive into.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receiver_credit_created_when_to_member_is_linked_with_account(
    session: AsyncSession, test_user, test_workspace
):
    receiver, _receiver_ws = await _make_user_with_workspace(
        session, "rx-with-acc@example.com"
    )
    receiver_account = await _make_account(session, receiver.id)
    payer_account = await _make_account(session, test_user.id)

    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="LinkedReceiver")
    )
    me = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    bob = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Bob", linked_user_id=receiver.id),
    )

    assert me is not None
    assert bob is not None

    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=me.id,
            to_member_id=bob.id,
            amount=Decimal("50.00"),
            currency="USD",
            date=date.today(),
            account_id=payer_account.id,
        ),
    )
    assert s is not None
    assert s.receiver_transaction_id is not None

    receiver_tx = await session.get(Transaction, s.receiver_transaction_id)
    assert receiver_tx is not None
    assert receiver_tx.user_id == receiver.id
    assert receiver_tx.account_id == receiver_account.id
    assert receiver_tx.type == "credit"
    assert receiver_tx.amount == Decimal("50.00")
    assert receiver_tx.currency == "USD"
    assert receiver_tx.source == "settlement"


@pytest.mark.asyncio
async def test_receiver_credit_skipped_when_to_member_is_shadow(
    session: AsyncSession, test_user, test_workspace
):
    """Shadow member (no linked_user_id, not is_self) — there's no real
    user on the receiving side, so no credit transaction is created."""
    payer_account = await _make_account(session, test_user.id)
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="ShadowReceiver")
    )
    me = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    shadow = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Shadow")
    )

    assert me is not None
    assert shadow is not None

    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=me.id,
            to_member_id=shadow.id,
            amount=Decimal("10.00"),
            currency="USD",
            date=date.today(),
            account_id=payer_account.id,
        ),
    )
    assert s is not None
    assert s.receiver_transaction_id is None


@pytest.mark.asyncio
async def test_receiver_credit_skipped_when_linked_user_has_no_cash_account(
    session: AsyncSession, test_user, test_workspace
):
    """Linked receiver, but their only account is a credit card (not
    checking/savings). The mirror credit can't be placed and is silently
    skipped — settlement is still recorded."""
    receiver, _receiver_ws = await _make_user_with_workspace(
        session, "rx-cc-only@example.com"
    )
    await _make_account(session, receiver.id, account_type="credit_card")
    payer_account = await _make_account(session, test_user.id)

    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="CCOnly")
    )
    me = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    bob = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Bob", linked_user_id=receiver.id),
    )

    assert me is not None
    assert bob is not None

    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=me.id,
            to_member_id=bob.id,
            amount=Decimal("10.00"),
            currency="USD",
            date=date.today(),
            account_id=payer_account.id,
        ),
    )
    assert s is not None
    assert s.receiver_transaction_id is None


@pytest.mark.asyncio
async def test_receiver_credit_lands_on_owner_when_self_member_unlinked(
    session: AsyncSession, test_user, test_workspace
):
    """When the to_member is the owner's self-member with no
    linked_user_id, the service falls back to group.user_id — the
    credit lands on the owner's checking account."""
    owner_account = await _make_account(session, test_user.id)
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="SelfFallback")
    )
    owner_self = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Me", is_self=True),
    )
    friend = await group_service.create_member(
        session, group.id, test_workspace.id,         GroupMemberCreate(name="Friend")
    )

    assert owner_self is not None
    assert friend is not None

    # Friend pays the owner back. Caller is the owner (allowed to record
    # on any from_member). No account_id — friend is a shadow, no real
    # account to debit. Receiver fallback should still kick in.
    s = await settlement_service.create_settlement(
        session,
        group.id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=friend.id,
            to_member_id=owner_self.id,
            amount=Decimal("20.00"),
            currency="USD",
            date=date.today(),
        ),
    )
    assert s is not None
    assert s.receiver_transaction_id is not None

    receiver_tx = await session.get(Transaction, s.receiver_transaction_id)
    assert receiver_tx is not None
    assert receiver_tx.user_id == test_user.id
    assert receiver_tx.account_id == owner_account.id
    assert receiver_tx.type == "credit"
    assert receiver_tx.amount == Decimal("20.00")
