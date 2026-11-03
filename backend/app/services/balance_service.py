"""Group balance computation.

Each split transaction was paid by one member: the member linked to the
Securo user who recorded it (`Transaction.user_id`), falling back to the
group's stored `is_self` member and then to the member linked to the group
owner. Every other member's split share is a debt from that member to the
payer. Settlements reduce the debt of `from` towards `to`.

That gives one pairwise ledger per group, the same for everyone. The
response projects it onto the caller (`self_member`, the caller's own
member): for each other member M, per currency, a positive amount means M
owes the caller and a negative amount means the caller owes M. Pairs that
don't involve the caller are left out, and so are a member's pairs with
an owner who isn't a member of the group.

Mixed currencies are kept as separate lines when no historical billing
amount is available. Transactions with a persisted primary-currency amount
are represented in that billing currency for settlement matching.
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
from app.models.user import User

# Stands in for a group owner who isn't one of the group's members.
_GROUP_OWNER = uuid.UUID(int=0)


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

    # get_group_visible projects `is_self` onto the caller in memory, so
    # read the stored flag with a column select (it bypasses the identity
    # map): it marks the owner's own member.
    stored_self_ids = set(
        (
            await session.execute(
                select(GroupMember.id).where(
                    GroupMember.group_id == group_id,
                    GroupMember.is_self.is_(True),
                )
            )
        ).scalars().all()
    )
    member_by_user = {m.linked_user_id: m.id for m in members if m.linked_user_id is not None}
    owner_self = next((m for m in members if m.id in stored_self_ids), None)

    # The caller's own member, by the same rule as group_service's "(you)"
    # tag: the member linked to the caller, else the owner's stored
    # unlinked self member when the caller is the owner.
    self_member = next((m for m in members if m.linked_user_id == user_id), None)
    if self_member is None and user_id == group.user_id and owner_self is not None:
        if owner_self.linked_user_id is None:
            self_member = owner_self

    default_payer = owner_self.id if owner_self is not None else None
    if default_payer is None:
        default_payer = member_by_user.get(group.user_id)
    if default_payer is None:
        # The owner recorded it but isn't a member of the group: the shares
        # are owed to the owner, who sees them as before.
        default_payer = _GROUP_OWNER

    # (debtor, creditor) -> currency -> Decimal
    owed: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )

    # Splits: each member's share of a transaction someone else paid is
    # what they owe the payer.
    from app.core.config import get_settings

    fallback_currency = get_settings().default_currency
    splits_result = await session.execute(
        select(
            TransactionSplit,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
            Transaction.user_id,
            Transaction.type,
            User.preferences,
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .join(GroupMember, TransactionSplit.group_member_id == GroupMember.id)
        .outerjoin(User, Transaction.user_id == User.id)
        .where(GroupMember.group_id == group_id)
    )
    split_rows = splits_result.all()
    converted_shares: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
    rows_by_transaction: dict[uuid.UUID, list[tuple]] = defaultdict(list)
    for row in split_rows:
        rows_by_transaction[row[0].transaction_id].append(row)

    for transaction_id, transaction_rows in rows_by_transaction.items():
        first_row = transaction_rows[0]
        transaction_amount = first_row[1]
        amount_primary = first_row[2]
        transaction_currency = first_row[3]
        preferences = first_row[6]
        primary_currency = (preferences or {}).get(
            "currency_display", fallback_currency
        )
        if (
            amount_primary is not None
            and primary_currency == group.default_currency
            and transaction_currency != group.default_currency
            and transaction_amount
        ):
            target_amount = abs(Decimal(str(amount_primary))).quantize(Decimal("0.01"))
            source_amount = abs(Decimal(str(transaction_amount)))
            converted = [
                (
                    row[0].group_member_id,
                    (
                        target_amount
                        * Decimal(str(row[0].share_amount))
                        / source_amount
                    ).quantize(Decimal("0.01")),
                )
                for row in transaction_rows
            ]
            residual = target_amount - sum(
                (share for _, share in converted), Decimal("0")
            )
            member_id, last_share = converted[-1]
            converted[-1] = (member_id, last_share + residual)
            converted_shares[transaction_id] = dict(converted)

    for (
        split,
        transaction_amount,
        amount_primary,
        currency,
        recorder_id,
        tx_type,
        preferences,
    ) in split_rows:
        recorder = member_by_user.get(recorder_id, default_payer)
        if split.group_member_id == recorder:
            continue
        share = Decimal(str(split.share_amount))
        primary_currency = (preferences or {}).get(
            "currency_display", fallback_currency
        )
        # Preserve the historical conversion stored on the transaction when
        # the group's default currency is the user's primary currency.
        if (
            amount_primary is not None
            and primary_currency == group.default_currency
            and currency != group.default_currency
            and transaction_amount
        ):
            share = converted_shares[split.transaction_id][split.group_member_id]
            currency = group.default_currency
        if tx_type == "credit":
            # Income: the recorder received the money, so they owe each
            # other member that member's share.
            owed[(recorder, split.group_member_id)][currency] += share
        else:
            owed[(split.group_member_id, recorder)][currency] += share

    # Settlements: `from` paid `to`, which reduces what `from` owes `to`.
    settlements_result = await session.execute(
        select(GroupSettlement).where(GroupSettlement.group_id == group_id)
    )
    for s in settlements_result.scalars().all():
        owed[(s.from_member_id, s.to_member_id)][s.currency] -= Decimal(str(s.amount))

    # Project onto the caller: member_id -> currency -> what they owe the caller.
    totals: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    caller = self_member.id if self_member is not None else _GROUP_OWNER
    for (debtor, creditor), by_currency in owed.items():
        # A member can't be shown a debt to or from an owner who has no
        # member row: there is no name or GroupMember id to show it under.
        if self_member is not None and _GROUP_OWNER in (debtor, creditor):
            continue
        for currency, amount in by_currency.items():
            if creditor == caller:
                totals[debtor][currency] += amount
            elif debtor == caller:
                totals[creditor][currency] -= amount

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
                converted, _ = await _fx_convert(
                    session, amount, currency, default_currency
                )
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
