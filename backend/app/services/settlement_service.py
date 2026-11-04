import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, select
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
    if account.user_id != user_id:
        raise ValueError("Payment account must belong to the settlement payer")

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
    session: AsyncSession,
    user_id: uuid.UUID,
    preferred_workspace_id: Optional[uuid.UUID] = None,
) -> Optional[Account]:
    """Return the user's first non-archived checking/savings account
    across any workspace they belong to. Used as the auto-target for
    receiver-side settlement credits — the receiver may sit in a
    different workspace than where the settlement was recorded.

    Accounts in `preferred_workspace_id` (the group's workspace) come
    first, so a user with several workspaces is credited where the group
    lives rather than wherever the alphabetically first account is (#1143).
    """
    from app.models.workspace import WorkspaceMember

    # Resolve the workspaces the user can write to. Pick the first
    # account the user owns in any of them; ties broken by name. A shared
    # workspace also holds other members' accounts, which must never
    # receive this user's credit.
    user_workspaces_subq = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user_id
    )
    result = await session.execute(
        select(Account)
        .where(
            Account.user_id == user_id,
            Account.workspace_id.in_(user_workspaces_subq),
            Account.is_closed.is_(False),
            Account.type.in_(("checking", "savings")),
        )
        .order_by(
            case((Account.workspace_id == preferred_workspace_id, 0), else_=1),
            Account.name,
        )
    )
    return result.scalars().first()


async def _create_receiver_credit(
    session: AsyncSession,
    receiver_user_id: uuid.UUID,
    amount,
    currency: str,
    when,
    description: str,
    preferred_workspace_id: Optional[uuid.UUID] = None,
) -> Optional[Transaction]:
    """Mirror a settlement credit on the receiver's side.

    Picks the receiver's first checking/savings account, preferring the
    group's workspace, and stamps the credit there. Returns None
    silently when the receiver has no suitable account — they can do
    it manually or the next time they reconcile from their bank sync.
    """
    account = await _pick_default_account_for_user(
        session, receiver_user_id, preferred_workspace_id
    )
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


async def _validate_payer_debit(
    session: AsyncSession,
    transaction_id: Optional[uuid.UUID],
    workspace_id: uuid.UUID,
    group: Group,
    from_member_id: uuid.UUID,
    amount: Decimal,
    currency: str,
) -> None:
    if transaction_id is None:
        return
    result = await session.execute(
        select(Transaction, Account.user_id, GroupMember.linked_user_id, GroupMember.is_self)
        .join(Account, Transaction.account_id == Account.id)
        .join(GroupMember, GroupMember.id == from_member_id)
        .where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == workspace_id,
            Account.workspace_id == workspace_id,
            GroupMember.group_id == group.id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise ValueError("Linked transaction not found")
    transaction, account_user_id, payer_user_id, payer_is_self = row
    if transaction.type != "debit":
        raise ValueError("Linked transaction must be a payer debit")
    expected_payer_id = payer_user_id or (group.user_id if payer_is_self else None)
    if expected_payer_id is None or account_user_id != expected_payer_id:
        raise ValueError("Linked transaction must belong to the settlement payer")
    if transaction.currency == currency and abs(Decimal(str(transaction.amount))) != Decimal(
        str(amount)
    ):
        raise ValueError("Linked payer debit amount must match the settlement amount")


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
    await _validate_payer_debit(
        session,
        data.transaction_id,
        workspace_id,
        group,
        data.from_member_id,
        data.amount,
        data.currency,
    )

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
        payer_meta = member_meta.get(data.from_member_id)
        expected_payer_user_id = (
            payer_meta.linked_user_id
            if payer_meta is not None
            else None
        )
        if expected_payer_user_id is None and payer_meta is not None and payer_meta.is_self:
            expected_payer_user_id = group.user_id
        if expected_payer_user_id is None:
            raise ValueError("Cannot create a payment for an unlinked settlement payer")
        auto_desc = description or f"Acerto · {group.name} · {to_name}"
        tx = await _create_payment_transaction(
            session,
            expected_payer_user_id,
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
    # Only mirror a cash receipt when this settlement also records a payer
    # transaction. Administrative ledger-only settlements must not invent
    # income in the receiver's account.
    if receiver_user_id is not None and payload.get("transaction_id") is not None:
        receiver_desc = description or f"Acerto · {group.name} · {from_name}"
        receiver_tx = await _create_receiver_credit(
            session,
            receiver_user_id,
            data.amount,
            data.currency,
            data.date,
            receiver_desc,
            preferred_workspace_id=group.workspace_id,
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
        select(GroupSettlement)
        .where(
            GroupSettlement.id == settlement_id,
            GroupSettlement.group_id == group_id,
        )
        .with_for_update()
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

    payer_transaction_id = update_data.get("transaction_id", settlement.transaction_id)
    if "transaction_id" in update_data or (
        payer_transaction_id is not None
        and {"amount", "currency", "from_member_id"}.intersection(update_data)
    ):
        await _validate_payer_debit(
            session,
            payer_transaction_id,
            workspace_id,
            group,
            update_data.get("from_member_id", settlement.from_member_id),
            update_data.get("amount", settlement.amount),
            update_data.get("currency", settlement.currency),
        )
        if (
            "transaction_id" in update_data
            and update_data["transaction_id"] is None
            and settlement.receiver_transaction_id
        ):
            raise ValueError("Cannot unlink the payer transaction while a receiver credit exists")

    if settlement.receiver_transaction_id and "to_member_id" in update_data:
        raise ValueError(
            "Cannot change the receiver while a receiver credit exists. "
            "Have the receiver delete the linked settlement credit from their "
            "transaction list, then retry."
        )

    receiver_tx = None
    if settlement.receiver_transaction_id and {"amount", "currency", "date"}.intersection(
        update_data
    ):
        receiver_result = await session.execute(
            select(Transaction)
            .where(Transaction.id == settlement.receiver_transaction_id)
            .with_for_update()
        )
        receiver_tx = receiver_result.scalar_one_or_none()
        if (
            receiver_tx is None
            or receiver_tx.external_id
            or receiver_tx.import_id
        ):
            raise ValueError(
                "This receiver credit has a bank/import match. Have the receiver "
                "unlink that match before editing the settlement."
            )

    for key, value in update_data.items():
        setattr(settlement, key, value)

    if receiver_tx is not None:
        receiver_tx.amount = settlement.amount
        receiver_tx.currency = settlement.currency
        receiver_tx.date = settlement.date
        receiver_tx.amount_primary = None
        receiver_tx.amount_primary_currency = None
        receiver_tx.fx_rate_used = None
        from app.services.fx_rate_service import stamp_primary_amount

        await stamp_primary_amount(session, receiver_tx.user_id, receiver_tx)

    if (
        "transaction_id" in update_data
        and settlement.transaction_id is not None
        and settlement.receiver_transaction_id is None
    ):
        receiver_meta_result = await session.execute(
            select(GroupMember.linked_user_id, GroupMember.is_self).where(
                GroupMember.id == settlement.to_member_id
            )
        )
        receiver_meta = receiver_meta_result.one_or_none()
        receiver_user_id = receiver_meta[0] if receiver_meta else None
        if receiver_user_id is None and receiver_meta and receiver_meta[1]:
            receiver_user_id = group.user_id
        if receiver_user_id is not None:
            receiver_tx = await _create_receiver_credit(
                session,
                receiver_user_id,
                settlement.amount,
                settlement.currency,
                settlement.date,
                f"Acerto · {group.name}",
                preferred_workspace_id=group.workspace_id,
            )
            if receiver_tx is not None:
                settlement.receiver_transaction_id = receiver_tx.id

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
