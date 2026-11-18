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
from datetime import date
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit

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
    default_currency = group.default_currency

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
    splits_result = await session.execute(
        select(
            TransactionSplit,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.amount_primary_currency,
            Transaction.currency,
            Transaction.user_id,
            Transaction.type,
            Transaction.date,
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .join(GroupMember, TransactionSplit.group_member_id == GroupMember.id)
        .where(GroupMember.group_id == group_id)
        .order_by(
            TransactionSplit.transaction_id,
            TransactionSplit.group_member_id,
        )
    )
    split_rows = splits_result.all()
    converted_shares: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
    legacy_conversion_tranches: dict[tuple[uuid.UUID, uuid.UUID, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    native_debt_tranches: dict[tuple[uuid.UUID, uuid.UUID, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    rows_by_transaction: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for row in split_rows:
        rows_by_transaction[row[0].transaction_id].append(row)

    for transaction_id, transaction_rows in rows_by_transaction.items():
        first_row = transaction_rows[0]
        transaction_amount = first_row[1]
        amount_primary = first_row[2]
        amount_primary_currency = first_row[3]
        transaction_currency = first_row[4]
        if (
            amount_primary is not None
            and amount_primary_currency == group.default_currency
            and transaction_currency != group.default_currency
            and transaction_amount
        ):
            target_amount = abs(Decimal(str(amount_primary))).quantize(Decimal("0.01"))
            source_amount = abs(Decimal(str(transaction_amount)))
            converted = [
                (
                    row[0].group_member_id,
                    (target_amount * Decimal(str(row[0].share_amount)) / source_amount).quantize(
                        Decimal("0.01")
                    ),
                )
                for row in transaction_rows
            ]
            # Split rows can intentionally cover only part of the transaction
            # (for example after an amount-only edit). Allocate cents only
            # across the amount represented by the stored split rows.
            covered_amount = sum(
                (Decimal(str(row[0].share_amount)) for row in transaction_rows),
                Decimal("0"),
            )
            expected_total = (target_amount * covered_amount / source_amount).quantize(
                Decimal("0.01")
            )
            residual = expected_total - sum((share for _, share in converted), Decimal("0"))
            member_id, last_share = converted[-1]
            converted[-1] = (member_id, last_share + residual)
            converted_shares[transaction_id] = dict(converted)

    for (
        split,
        transaction_amount,
        amount_primary,
        amount_primary_currency,
        currency,
        recorder_id,
        tx_type,
        transaction_date,
    ) in split_rows:
        recorder = member_by_user.get(recorder_id, default_payer)
        if split.group_member_id == recorder:
            continue
        share = Decimal(str(split.share_amount))
        source_currency = currency
        # Preserve the historical conversion stored on the transaction when
        # its recorded currency matches the group's default currency.
        if (
            amount_primary is not None
            and amount_primary_currency == group.default_currency
            and currency != group.default_currency
            and transaction_amount
        ):
            share = converted_shares[split.transaction_id][split.group_member_id]
            currency = group.default_currency
            debtor, creditor = (
                (recorder, split.group_member_id)
                if tx_type == "credit"
                else (split.group_member_id, recorder)
            )
            legacy_conversion_tranches[(debtor, creditor, source_currency)].append(
                {
                    "date": transaction_date,
                    "transaction_id": split.transaction_id,
                    "source_remaining": Decimal(str(split.share_amount)),
                    "source_total": Decimal(str(split.share_amount)),
                    "target_total": share,
                    "target_remaining": share,
                }
            )
        else:
            debtor, creditor = (
                (recorder, split.group_member_id)
                if tx_type == "credit"
                else (split.group_member_id, recorder)
            )
            native_debt_tranches[(debtor, creditor, currency)].append(
                {
                    "date": transaction_date,
                    "transaction_id": split.transaction_id,
                    "remaining": share,
                }
            )
        if tx_type == "credit":
            # Income: the recorder received the money, so they owe each
            # other member that member's share.
            owed[(recorder, split.group_member_id)][currency] += share
        else:
            owed[(split.group_member_id, recorder)][currency] += share

    # Settlements: `from` paid `to`, which reduces what `from` owes `to`.
    settlements_result = await session.execute(
        select(GroupSettlement)
        .where(GroupSettlement.group_id == group_id)
        .order_by(GroupSettlement.date, GroupSettlement.created_at)
    )
    settlements = settlements_result.scalars().all()
    def tranche_order(tranche: dict[str, Any]) -> tuple[date, uuid.UUID]:
        return tranche["date"], tranche["transaction_id"]

    for tranches in native_debt_tranches.values():
        tranches.sort(key=tranche_order)

    legacy_tranches_by_pair: dict[tuple[uuid.UUID, uuid.UUID], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for (debtor, creditor, _), tranches in legacy_conversion_tranches.items():
        tranches.sort(key=tranche_order)
        legacy_tranches_by_pair[(debtor, creditor)].extend(tranches)
    for tranches in legacy_tranches_by_pair.values():
        tranches.sort(key=tranche_order)

    for s in settlements:
        pair = (s.from_member_id, s.to_member_id)
        remaining = Decimal(str(s.amount))
        # Keep legacy same-currency debts and settlements together first.
        # If the settlement exceeds that native debt, apply the remainder to
        # converted obligations using the weighted historical conversion
        # represented by those obligations, rather than today's FX rate.
        native_tranches = native_debt_tranches.get((pair[0], pair[1], s.currency), [])
        for tranche in native_tranches:
            if remaining == 0:
                break
            if tranche["date"] > s.date or tranche["remaining"] <= 0:
                continue
            native_payment = min(remaining, tranche["remaining"])
            tranche["remaining"] -= native_payment
            owed[pair][s.currency] -= native_payment
            remaining -= native_payment

        if s.currency == default_currency:
            converted_tranches = [
                tranche
                for tranche in legacy_tranches_by_pair.get(pair, [])
                if tranche["date"] <= s.date and tranche["target_remaining"] > 0
            ]
            total_target_remaining = sum(
                (tranche["target_remaining"] for tranche in converted_tranches),
                Decimal("0"),
            )
            target_to_allocate = min(remaining, total_target_remaining)
            target_allocations: list[Decimal] = []
            allocated_target = Decimal("0")
            for index, tranche in enumerate(converted_tranches):
                if index == len(converted_tranches) - 1:
                    target_payment = min(
                        tranche["target_remaining"], target_to_allocate - allocated_target
                    )
                else:
                    target_payment = min(
                        tranche["target_remaining"],
                        (
                            target_to_allocate
                            * tranche["target_remaining"]
                            / total_target_remaining
                        ).quantize(Decimal("0.01"), rounding=ROUND_DOWN),
                    )
                target_allocations.append(target_payment)
                allocated_target += target_payment

            # Distribute cents left by rounding without allowing any tranche
            # to be paid beyond its converted balance.
            unallocated_target = target_to_allocate - allocated_target
            for index, tranche in enumerate(converted_tranches):
                if unallocated_target <= 0:
                    break
                room = tranche["target_remaining"] - target_allocations[index]
                extra = min(room, unallocated_target)
                target_allocations[index] += extra
                unallocated_target -= extra

            for tranche, target_payment in zip(converted_tranches, target_allocations):
                if target_payment <= 0:
                    continue
                new_target_remaining = tranche["target_remaining"] - target_payment
                if new_target_remaining <= 0:
                    new_source_remaining = Decimal("0")
                elif tranche["target_total"]:
                    new_source_remaining = (
                        tranche["source_total"]
                        * new_target_remaining
                        / tranche["target_total"]
                    )
                else:
                    new_source_remaining = tranche["source_remaining"]
                tranche["target_remaining"] = new_target_remaining
                tranche["source_remaining"] = min(
                    tranche["source_remaining"], new_source_remaining
                )
                owed[pair][default_currency] -= target_payment
                remaining -= target_payment
            if remaining:
                owed[pair][default_currency] -= remaining
            continue

        tranches = legacy_conversion_tranches.get((s.from_member_id, s.to_member_id, s.currency))
        if remaining == 0:
            continue
        eligible = [
            tranche
            for tranche in tranches or ()
            if tranche["date"] <= s.date and tranche["source_remaining"] > 0
        ]
        eligible_source_total = sum(
            (tranche["source_remaining"] for tranche in eligible), Decimal("0")
        )
        source_to_allocate = min(remaining, eligible_source_total)
        converted_payment = Decimal("0")
        left_to_allocate = source_to_allocate

        def apply_source_payment(tranche: dict[str, Any], source_payment: Decimal) -> Decimal:
            if source_payment <= 0:
                return Decimal("0")
            old_source_remaining = tranche["source_remaining"]
            old_target_remaining = tranche["target_remaining"]
            new_source_remaining = old_source_remaining - source_payment
            if new_source_remaining <= 0:
                target_payment = old_target_remaining
                new_source_remaining = Decimal("0")
            elif tranche["source_total"]:
                new_target_remaining = (
                    tranche["target_total"]
                    * new_source_remaining
                    / tranche["source_total"]
                ).quantize(Decimal("0.01"))
                target_payment = max(Decimal("0"), old_target_remaining - new_target_remaining)
            else:
                target_payment = Decimal("0")
            tranche["source_remaining"] = new_source_remaining
            tranche["target_remaining"] = old_target_remaining - target_payment
            return target_payment

        for index, tranche in enumerate(eligible):
            if left_to_allocate <= 0:
                break
            if index == len(eligible) - 1 or source_to_allocate == eligible_source_total:
                source_payment = min(left_to_allocate, tranche["source_remaining"])
            else:
                source_payment = min(
                    tranche["source_remaining"],
                    (
                        source_to_allocate * tranche["source_remaining"] / eligible_source_total
                    ).quantize(Decimal("0.01")),
                )
            if source_payment <= 0:
                continue
            target_payment = apply_source_payment(tranche, source_payment)
            converted_payment += target_payment
            left_to_allocate -= source_payment

        # Per-tranche cent rounding can leave part of this settlement
        # unassigned. Spend that remainder on any eligible tranche that still
        # has source currency outstanding, in stable date/transaction order.
        if left_to_allocate > 0:
            for tranche in eligible:
                if left_to_allocate <= 0:
                    break
                source_payment = min(left_to_allocate, tranche["source_remaining"])
                target_payment = apply_source_payment(tranche, source_payment)
                converted_payment += target_payment
                left_to_allocate -= source_payment
        remaining -= source_to_allocate - left_to_allocate
        if converted_payment:
            owed[pair][default_currency] -= converted_payment
        if remaining:
            owed[pair][s.currency] -= remaining

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
