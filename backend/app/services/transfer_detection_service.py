import uuid
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def detect_transfer_pairs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    candidate_ids: Optional[list[uuid.UUID]] = None,
    date_tolerance_days: int = 2,
) -> int:
    """Detect inter-account transfer pairs and link them with a shared UUID.

    Algorithm:
    1. When candidate_ids is given, load candidate debits AND candidate credits
       so that detection works regardless of which side was just imported.
    2. For each debit, find an unpaired credit with: same user, different account,
       same absolute amount, date within ±tolerance days
    3. Greedy closest-date-first matching; each tx can only pair once

    Returns the number of pairs created.
    """
    # Load candidate debits — filtered to candidate_ids when provided
    debit_query = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.type == "debit",
        Transaction.transfer_pair_id.is_(None),
        Transaction.source != "opening_balance",
    )
    if candidate_ids:
        debit_query = debit_query.where(Transaction.id.in_(candidate_ids))

    debit_result = await session.execute(debit_query)
    debits = list(debit_result.scalars().all())

    # When candidate_ids is given, also load ALL unpaired debits that could
    # match new credits (the reverse direction).
    if candidate_ids:
        reverse_debit_query = select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
            Transaction.id.not_in(candidate_ids),
        )
        reverse_result = await session.execute(reverse_debit_query)
        reverse_debits = list(reverse_result.scalars().all())
    else:
        reverse_debits = []

    all_debits = debits + reverse_debits

    if not all_debits:
        return 0

    # Load all unpaired credits for the user (potential partners)
    credit_query = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.type == "credit",
        Transaction.transfer_pair_id.is_(None),
        Transaction.source != "opening_balance",
    )
    credit_result = await session.execute(credit_query)
    credits = list(credit_result.scalars().all())

    if not credits:
        return 0

    # When candidate_ids is given, restrict reverse debits to only match
    # credits that are in candidate_ids (avoid pairing two old transactions).
    candidate_id_set = set(candidate_ids) if candidate_ids else None

    # ponytail: amount-bucketed iterative closest-date matching;
    # skipped: description-based disambiguation, add when description NLP/heuristics needed.
    debits_by_amount: dict[float, list[Transaction]] = defaultdict(list)
    for d in all_debits:
        debits_by_amount[abs(float(d.amount))].append(d)

    credits_by_amount: dict[float, list[Transaction]] = defaultdict(list)
    for c in credits:
        credits_by_amount[abs(float(c.amount))].append(c)

    pairs_created = 0

    def _is_valid(debit: Transaction, credit: Transaction) -> bool:
        if credit.account_id == debit.account_id:
            return False
        if candidate_id_set and debit.id not in candidate_id_set and credit.id not in candidate_id_set:
            return False
        return abs((credit.date - debit.date).days) <= date_tolerance_days

    for amount, group_debits in debits_by_amount.items():
        group_credits = credits_by_amount.get(amount)
        if not group_credits:
            continue

        unpaired_debits = list(group_debits)
        unpaired_credits = list(group_credits)

        while unpaired_debits and unpaired_credits:
            # Find unique closest credit for each debit
            best_credit_for_debit: dict[uuid.UUID, Optional[Transaction]] = {}
            for d in unpaired_debits:
                min_delta: Optional[int] = None
                best_c: Optional[Transaction] = None
                tied = False
                for c in unpaired_credits:
                    if not _is_valid(d, c):
                        continue
                    delta = abs((c.date - d.date).days)
                    if min_delta is None or delta < min_delta:
                        min_delta = delta
                        best_c = c
                        tied = False
                    elif delta == min_delta:
                        tied = True
                best_credit_for_debit[d.id] = best_c if (best_c and not tied) else None

            # Find unique closest debit for each credit
            best_debit_for_credit: dict[uuid.UUID, Optional[Transaction]] = {}
            for c in unpaired_credits:
                min_delta: Optional[int] = None
                best_d: Optional[Transaction] = None
                tied = False
                for d in unpaired_debits:
                    if not _is_valid(d, c):
                        continue
                    delta = abs((c.date - d.date).days)
                    if min_delta is None or delta < min_delta:
                        min_delta = delta
                        best_d = d
                        tied = False
                    elif delta == min_delta:
                        tied = True
                best_debit_for_credit[c.id] = best_d if (best_d and not tied) else None

            # Commit only when the winner is mutually unique
            matched_debit_ids: set[uuid.UUID] = set()
            matched_credit_ids: set[uuid.UUID] = set()

            for d in unpaired_debits:
                c = best_credit_for_debit.get(d.id)
                if not c:
                    continue
                if best_debit_for_credit.get(c.id) == d:
                    pair_id = uuid.uuid4()
                    d.transfer_pair_id = pair_id
                    c.transfer_pair_id = pair_id
                    matched_debit_ids.add(d.id)
                    matched_credit_ids.add(c.id)
                    pairs_created += 1

            if not matched_debit_ids:
                break

            unpaired_debits = [d for d in unpaired_debits if d.id not in matched_debit_ids]
            unpaired_credits = [c for c in unpaired_credits if c.id not in matched_credit_ids]

    return pairs_created


async def unlink_transfer_pair(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    pair_id: uuid.UUID,
) -> int:
    """Remove a transfer pair link. Returns number of transactions unlinked."""
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.transfer_pair_id == pair_id,
        )
    )
    transactions = list(result.scalars().all())

    for tx in transactions:
        tx.transfer_pair_id = None

    return len(transactions)
