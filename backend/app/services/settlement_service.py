import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.transaction import Transaction
from app.schemas.group_settlement import (
    GroupSettlementCreate,
    GroupSettlementUpdate,
)


async def _ensure_group_visible(
    session: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[Group]:
    """Visible when the group lives in the current workspace OR the
    caller is linked as a cross-workspace member — for read endpoints."""
    from app.services.group_service import get_group_visible

    return await get_group_visible(session, group_id, workspace_id, user_id)


async def _user_member_id(
    session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """If the user is a linked member of this group, return that member
    id. Owners may not have a linked member (they can still act via
    the owner check), so this can return None for them."""
    result = await session.execute(
        select(GroupMember.id).where(
            GroupMember.group_id == group_id,
            GroupMember.linked_user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _can_settle_from(
    session: AsyncSession,
    group: Group,
    user_id: uuid.UUID,
    from_member_id: uuid.UUID,
) -> bool:
    """Permission check for creating/editing a settlement:
    - Group owner can do anything.
    - Linked member can only act when they are the `from_member`
      (i.e., they're recording a payment they themselves made)."""
    if group.user_id == user_id:
        return True
    linked = await _user_member_id(session, group.id, user_id)
    return linked is not None and linked == from_member_id


async def _create_payment_transaction(
    session: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    amount,
    currency: str,
    when,
    description: str,
) -> Transaction:
    """Create a debit transaction on the user's account representing a
    settlement payment. Validates that the account belongs to the
    current workspace."""
    account_result = await session.execute(
        select(Account).where(
            Account.id == account_id,
            Account.workspace_id == workspace_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if account is None:
        raise ValueError("Account not found")

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        description=description,
        amount=amount,
        currency=currency,
        date=when,
        type="debit",
        # `settlement` is a special source that excludes the row from
        # spending reports — the underlying expense is already counted
        # via the share that produced the debt; this is the payback,
        # not a new expense.
        source="settlement",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    # Stamp primary-currency amount so dashboard / report aggregations
    # that prefer amount_primary include this row.
    from app.services.fx_rate_service import stamp_primary_amount

    await stamp_primary_amount(session, user_id, tx)
    return tx


async def _pick_default_account_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Optional[Account]:
    """Return the user's first non-archived checking/savings account
    across any workspace they belong to. Used as the auto-target for
    receiver-side settlement credits — the receiver may sit in a
    different workspace than where the settlement was recorded."""
    from app.models.workspace import WorkspaceMember

    # Resolve the workspaces the user can write to. Pick the first
    # account that lives in any of them; ties broken by name.
    user_workspaces_subq = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user_id
    )
    result = await session.execute(
        select(Account)
        .where(
            Account.workspace_id.in_(user_workspaces_subq),
            Account.is_closed.is_(False),
            Account.type.in_(("checking", "savings")),
        )
        .order_by(Account.name)
    )
    return result.scalars().first()


async def _create_receiver_credit(
    session: AsyncSession,
    receiver_user_id: uuid.UUID,
    amount,
    currency: str,
    when,
    description: str,
) -> Optional[Transaction]:
    """Mirror a settlement credit on the receiver's side.

    Picks the receiver's first checking/savings account from any
    workspace they belong to and stamps the credit there. Returns None
    silently when the receiver has no suitable account — they can do
    it manually or the next time they reconcile from their bank sync.
    """
    account = await _pick_default_account_for_user(session, receiver_user_id)
    if account is None:
        return None
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=receiver_user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=amount,
        currency=currency,
        date=when,
        type="credit",
        # Settlement credits ARE counted in P/L (income), unlike the
        # payer's debit which is excluded. See counts_as_pnl().
        source="settlement",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    from app.services.fx_rate_service import stamp_primary_amount

    await stamp_primary_amount(session, receiver_user_id, tx)
    return tx


async def _validate_members_in_group(
    session: AsyncSession, group_id: uuid.UUID, member_ids: list[uuid.UUID]
) -> None:
    result = await session.execute(
        select(GroupMember.id).where(
            GroupMember.group_id == group_id, GroupMember.id.in_(member_ids)
        )
    )
    found = {row[0] for row in result.all()}
    if found != set(member_ids):
        raise ValueError("Settlement members must belong to the group")


async def _validate_transaction(
    session: AsyncSession,
    transaction_id: Optional[uuid.UUID],
    workspace_id: uuid.UUID,
) -> None:
    if transaction_id is None:
        return
    result = await session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == workspace_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Linked transaction not found")


async def list_settlements(
    session: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[list[GroupSettlement]]:
    if not await _ensure_group_visible(session, group_id, workspace_id, user_id):
        return None
    result = await session.execute(
        select(GroupSettlement)
        .where(GroupSettlement.group_id == group_id)
        .order_by(GroupSettlement.date.desc(), GroupSettlement.created_at.desc())
    )
    return list(result.scalars().all())


async def create_settlement(
    session: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: GroupSettlementCreate,
) -> Optional[GroupSettlement]:
    group = await _ensure_group_visible(session, group_id, workspace_id, user_id)
    if not group:
        return None

    if not await _can_settle_from(session, group, user_id, data.from_member_id):
        # Linked members may only record payments they themselves made.
        raise PermissionError("You can only record settlements where you are the payer")

    await _validate_members_in_group(session, group_id, [data.from_member_id, data.to_member_id])
    await _validate_transaction(session, data.transaction_id, workspace_id)

    payload = data.model_dump()
    account_id = payload.pop("account_id", None)
    description = payload.pop("description", None)

    # Resolve member metadata once — we need names for descriptions
    # and the to_member's linked_user_id for the receiver-side credit.
    members_q = await session.execute(
        select(
            GroupMember.id,
            GroupMember.name,
            GroupMember.linked_user_id,
            GroupMember.is_self,
        ).where(GroupMember.id.in_([data.from_member_id, data.to_member_id]))
    )
    member_meta = {row.id: row for row in members_q.all()}
    from_name = member_meta[data.from_member_id].name if data.from_member_id in member_meta else "—"
    to_meta = member_meta.get(data.to_member_id)
    to_name = to_meta.name if to_meta else "—"
    # Resolve the receiver's Securo user id. linked_user_id wins; fall
    # back to group.user_id when the receiver is the owner's
    # self-member (owners often don't bother linking themselves).
    receiver_user_id = None
    if to_meta is not None:
        receiver_user_id = to_meta.linked_user_id
        if receiver_user_id is None and to_meta.is_self:
            receiver_user_id = group.user_id

    # Optional integration with the real account ledger: create a debit
    # transaction on the payer's account and link it via transaction_id.
    if account_id is not None:
        if payload.get("transaction_id") is not None:
            raise ValueError(
                "Pass either account_id (to create a transaction) or "
                "transaction_id (to link an existing one), not both"
            )
        auto_desc = description or f"Acerto · {group.name} · {to_name}"
        tx = await _create_payment_transaction(
            session,
            user_id,
            workspace_id,
            account_id,
            data.amount,
            data.currency,
            data.date,
            auto_desc,
        )
        payload["transaction_id"] = tx.id

    # Receiver-side mirror credit: when the receiver maps to a Securo
    # user and has a checking/savings account, record the cash-in side
    # so their books reflect the actual money received.
    receiver_tx_id = None
    if receiver_user_id is not None:
        receiver_desc = description or f"Acerto · {group.name} · {from_name}"
        receiver_tx = await _create_receiver_credit(
            session,
            receiver_user_id,
            data.amount,
            data.currency,
            data.date,
            receiver_desc,
        )
        if receiver_tx is not None:
            receiver_tx_id = receiver_tx.id
    payload["receiver_transaction_id"] = receiver_tx_id

    settlement = GroupSettlement(group_id=group_id, workspace_id=workspace_id, **payload)
    session.add(settlement)
    await session.commit()
    await session.refresh(settlement)
    return settlement


async def update_settlement(
    session: AsyncSession,
    group_id: uuid.UUID,
    settlement_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: GroupSettlementUpdate,
) -> Optional[GroupSettlement]:
    group = await _ensure_group_visible(session, group_id, workspace_id, user_id)
    if not group:
        return None

    result = await session.execute(
        select(GroupSettlement).where(
            GroupSettlement.id == settlement_id,
            GroupSettlement.group_id == group_id,
        )
    )
    settlement = result.scalar_one_or_none()
    if not settlement:
        return None

    # Caller must currently own the settlement (linked member of the
    # original from_member, or the group owner).
    if not await _can_settle_from(session, group, user_id, settlement.from_member_id):
        raise PermissionError("You can only edit settlements you created")

    update_data = data.model_dump(exclude_unset=True)

    new_from = update_data.get("from_member_id", settlement.from_member_id)
    new_to = update_data.get("to_member_id", settlement.to_member_id)
    if new_from == new_to:
        raise ValueError("from_member_id and to_member_id must differ")

    member_check: list[uuid.UUID] = []
    if "from_member_id" in update_data:
        member_check.append(update_data["from_member_id"])
    if "to_member_id" in update_data:
        member_check.append(update_data["to_member_id"])
    if member_check:
        await _validate_members_in_group(session, group_id, member_check)

    if "transaction_id" in update_data:
        await _validate_transaction(session, update_data["transaction_id"], workspace_id)

    for key, value in update_data.items():
        setattr(settlement, key, value)

    await session.commit()
    await session.refresh(settlement)
    return settlement


async def delete_settlement(
    session: AsyncSession,
    group_id: uuid.UUID,
    settlement_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    group = await _ensure_group_visible(session, group_id, workspace_id, user_id)
    if not group:
        return False
    result = await session.execute(
        select(GroupSettlement).where(
            GroupSettlement.id == settlement_id,
            GroupSettlement.group_id == group_id,
        )
    )
    settlement = result.scalar_one_or_none()
    if not settlement:
        return False
    if not await _can_settle_from(session, group, user_id, settlement.from_member_id):
        raise PermissionError("You can only delete settlements you created")
    await session.delete(settlement)
    await session.commit()
    return True
