"""Materializing transaction splits.

Splits are always written as integer-cent amounts (Numeric(15,2)) in the
transaction's currency. Equal/percent inputs are materialized at write
time and the rounding residual is assigned to the last share so the sum
matches the parent amount exactly.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group, GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.transaction_split import (
    TransactionSplitsInput,
)

_CENT = Decimal("0.01")


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _materialize(
    total: Decimal, payload: TransactionSplitsInput
) -> list[tuple[uuid.UUID, Decimal, Optional[Decimal]]]:
    """Resolve splits into [(member_id, share_amount, share_pct?)] tuples.

    `total` is treated as a positive value — the sign of a transaction
    (debit/credit) is independent of how it's distributed.
    """
    total = _quantize(Decimal(str(total)).copy_abs())
    n = len(payload.splits)
    if n == 0:
        raise ValueError("At least one split is required")

    if payload.share_type == "equal":
        per = _quantize(total / n)
        # Last share absorbs the rounding residual so the sum is exact.
        residual = total - (per * (n - 1))
        return [
            (s.group_member_id, residual if i == n - 1 else per, None)
            for i, s in enumerate(payload.splits)
        ]

    if payload.share_type == "exact":
        out: list[tuple[uuid.UUID, Decimal, Optional[Decimal]]] = []
        for s in payload.splits:
            if s.share_amount is None:
                raise ValueError("share_amount is required for share_type='exact'")
            out.append((s.group_member_id, _quantize(s.share_amount), None))
        if sum((a for _, a, _ in out), Decimal("0")) != total:
            raise ValueError("Split amounts must sum to the transaction amount")
        return out

    if payload.share_type == "percent":
        for s in payload.splits:
            if s.share_pct is None:
                raise ValueError("share_pct is required for share_type='percent'")
        pct_sum = sum((s.share_pct for s in payload.splits), Decimal("0"))
        if pct_sum != Decimal("100"):
            raise ValueError("Split percentages must sum to 100")

        amounts: list[Decimal] = []
        for i, s in enumerate(payload.splits):
            if i == n - 1:
                # Last share: residual so the sum is exact.
                amounts.append(total - sum(amounts, Decimal("0")))
            else:
                amounts.append(_quantize(total * s.share_pct / Decimal("100")))
        return [
            (s.group_member_id, amounts[i], s.share_pct)
            for i, s in enumerate(payload.splits)
        ]

    raise ValueError(f"Unknown share_type: {payload.share_type}")


async def _validate_members(
    session: AsyncSession,
    member_ids: list[uuid.UUID],
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    """Ensure all members belong to a single group VISIBLE to `user_id`
    (owner OR linked member). Returns that group's id."""
    # A group is visible if the user owns it or is linked as a member.
    linked_group_ids = (
        select(GroupMember.group_id)
        .where(GroupMember.linked_user_id == user_id)
        .distinct()
    )
    result = await session.execute(
        select(GroupMember, Group)
        .join(Group, GroupMember.group_id == Group.id)
        .where(
            GroupMember.id.in_(member_ids),
            Group.workspace_id == workspace_id,
            or_(Group.user_id == user_id, Group.id.in_(linked_group_ids)),
        )
    )
    rows = result.all()
    if len(rows) != len(set(member_ids)):
        raise ValueError("One or more split members not found")
    group_ids = {g.id for _, g in rows}
    if len(group_ids) != 1:
        raise ValueError("All splits must reference members of the same group")
    return group_ids.pop()


async def replace_splits(
    session: AsyncSession,
    transaction: Transaction,
    payload: Optional[TransactionSplitsInput],
    user_id: uuid.UUID,
) -> None:
    """Replace any existing splits on `transaction` with the given payload.

    Pass `payload=None` to leave splits untouched. Pass an empty
    `TransactionSplitsInput(splits=[])`-equivalent (handled upstream) to
    clear them.
    """
    if payload is None:
        return

    # Always start by clearing existing splits — replace semantics keep
    # the create/update paths trivially consistent.
    await session.execute(
        delete(TransactionSplit).where(
            TransactionSplit.transaction_id == transaction.id
        )
    )

    if not payload.splits:
        return

    member_ids = [s.group_member_id for s in payload.splits]
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("Each member can appear at most once per transaction")

    await _validate_members(session, member_ids, user_id, transaction.workspace_id)

    for member_id, share_amount, share_pct in _materialize(transaction.amount, payload):
        session.add(
            TransactionSplit(
                transaction_id=transaction.id,
                group_member_id=member_id,
                share_amount=share_amount,
                share_type=payload.share_type,
                share_pct=share_pct,
            )
        )
