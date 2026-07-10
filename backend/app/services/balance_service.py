"""Group balance computation.

The model assumes the group owner is the implicit payer of every
transaction with splits — i.e. the `is_self` member of the group is the
one whose bank account holds the parent transaction.

For each non-self member M, we compute, per currency:
    + sum of M's split shares on owner-paid transactions      (M owes self)
    + settlements where from=self, to=M                       (self lent to M)
    - settlements where from=M, to=self                       (M paid self back)

A positive value means M owes the owner; negative means the owner owes M.
Mixed currencies are kept as separate lines — no FX conversion in v1.
"""

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit


async def compute_balances(
    session: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[dict]:
    # Visible to anyone with read access to the group — workspace members
    # plus cross-workspace linked members. They all see the same
    # who-owes-whom view.
    from app.services.group_service import get_group_visible

    group = await get_group_visible(session, group_id, workspace_id, user_id)
    if not group:
        return None

    members_result = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group_id)
    )
    members = list(members_result.scalars().all())
    self_member = next((m for m in members if m.is_self), None)

    # member_id -> currency -> Decimal
    totals: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )

    # Splits — each non-self member's share of an owner-paid transaction
    # is what they owe the self member.
    splits_result = await session.execute(
        select(TransactionSplit, Transaction.currency)
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .join(GroupMember, TransactionSplit.group_member_id == GroupMember.id)
        .where(GroupMember.group_id == group_id)
    )
    for split, currency in splits_result.all():
        if self_member is not None and split.group_member_id == self_member.id:
            continue
        totals[split.group_member_id][currency] += Decimal(str(split.share_amount))

    # Settlements — adjust the pairwise balance with the self member.
    if self_member is not None:
        settlements_result = await session.execute(
            select(GroupSettlement).where(GroupSettlement.group_id == group_id)
        )
        for s in settlements_result.scalars().all():
            amount = Decimal(str(s.amount))
            if s.from_member_id == self_member.id:
                # Self lent to the other party — increases what they owe.
                totals[s.to_member_id][s.currency] += amount
            elif s.to_member_id == self_member.id:
                # Other party paid self back — reduces what they owe.
                totals[s.from_member_id][s.currency] -= amount
            # Cross-member settlements (neither side is self) don't affect
            # the owner's balance ledger and are ignored here.

    # FX-convert each line to the group's default currency so the
    # frontend can roll cross-currency lines up into a single KPI
    # without doing FX itself. Lines stay in their native currency too.
    from app.services.fx_rate_service import convert as _fx_convert

    default_currency = group.default_currency

    lines = []
    for member_id, by_currency in totals.items():
        for currency, amount in by_currency.items():
            if amount == 0:
                continue
            if currency == default_currency:
                amount_in_default = amount
            else:
                converted, _ = await _fx_convert(session, amount, currency, default_currency)
                amount_in_default = converted
            lines.append(
                {
                    "member_id": member_id,
                    "currency": currency,
                    "amount": amount,
                    "amount_in_default_currency": amount_in_default,
                }
            )

    return {
        "group_id": group_id,
        "self_member_id": self_member.id if self_member else None,
        "default_currency": default_currency,
        "lines": lines,
    }
