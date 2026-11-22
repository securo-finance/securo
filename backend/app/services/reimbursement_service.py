"""Reimbursements and refunds: a part of one transaction paying back another.

The anchor is the original row, the counterpart a row in the other direction
that pays part of it back (friends paying back a dinner, a refund for
returned clothes). Both rows stay as the bank reports them; user-level P&L
counts only the net (see `reimbursement_netting`, ADR 0031).

This module is the only writer of `reimbursement_allocations`, and it keeps
these invariants:

- an anchor and its counterparts have opposite directions and share a
  workspace,
- a row is never both an anchor and a counterpart, so links do not chain,
- the allocations out of a counterpart add up to at most its amount (an
  anchor has no cap: being paid back more than in full is allowed),
- neither side is a row P&L already leaves out or nets another way
  (transfers, ignored rows, settlements, group splits, ...).
"""

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Literal, Optional

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.mortgage_payment_allocation import MORTGAGE_PRINCIPAL_SOURCE
from app.models.reimbursement_allocation import ReimbursementAllocation
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.reimbursement import (
    ReimbursementAllocationInput,
    ReimbursementAllocationRead,
    ReimbursementLinkedTransaction,
    TransactionReimbursementsRead,
)

_CENT = Decimal("0.01")
_ZERO = Decimal("0")
# How far either side of a row the picker suggests rows without a search.
SUGGESTION_DAYS = 30

# Rows that are bookkeeping rather than spending or earning.
_UNLINKABLE_SOURCES = frozenset({"opening_balance", "settlement", MORTGAGE_PRINCIPAL_SOURCE})


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _magnitude(tx: Transaction) -> Decimal:
    return abs(Decimal(str(tx.amount or 0)))


async def link_block_reason(session: AsyncSession, tx: Transaction) -> Optional[str]:
    """Why `tx` cannot take part in a reimbursement link, or None when it can."""
    if tx.transfer_pair_id is not None:
        return "A transfer cannot be linked as a reimbursement"
    if tx.is_ignored or tx.exclude_from_pnl:
        return "A transaction left out of reports cannot be linked as a reimbursement"
    if tx.source in _UNLINKABLE_SOURCES:
        return "This transaction cannot be linked as a reimbursement"
    if not _magnitude(tx):
        return "A transaction with no amount cannot be linked as a reimbursement"
    if tx.category_id is not None:
        category = await session.get(Category, tx.category_id)
        if category is not None and (category.is_ignored or category.treat_as_transfer):
            return "A transaction in an ignored or transfer category cannot be linked as a reimbursement"
    has_group_split = await session.scalar(
        select(TransactionSplit.id).where(TransactionSplit.transaction_id == tx.id).limit(1)
    )
    if has_group_split is not None:
        return "A transaction split among people cannot be linked as a reimbursement"
    return None


async def is_linked(session: AsyncSession, transaction_id: uuid.UUID) -> bool:
    """Whether the row is on either side of an allocation."""
    found = await session.scalar(
        select(ReimbursementAllocation.id)
        .where(
            or_(
                ReimbursementAllocation.anchor_id == transaction_id,
                ReimbursementAllocation.counterpart_id == transaction_id,
            )
        )
        .limit(1)
    )
    return found is not None


async def allocated_from(
    session: AsyncSession,
    counterpart_ids: Iterable[uuid.UUID],
    *,
    excluding_anchor_id: Optional[uuid.UUID] = None,
) -> dict[uuid.UUID, Decimal]:
    """The total allocated out of each counterpart, in its own currency."""
    ids = list(set(counterpart_ids))
    if not ids:
        return {}
    stmt = (
        select(ReimbursementAllocation.counterpart_id, func.sum(ReimbursementAllocation.amount))
        .where(ReimbursementAllocation.counterpart_id.in_(ids))
        .group_by(ReimbursementAllocation.counterpart_id)
    )
    if excluding_anchor_id is not None:
        stmt = stmt.where(ReimbursementAllocation.anchor_id != excluding_anchor_id)
    result = await session.execute(stmt)
    return {cid: Decimal(str(total or 0)) for cid, total in result.all()}


async def replace_allocations(
    session: AsyncSession,
    anchor: Transaction,
    allocations: list[ReimbursementAllocationInput],
) -> None:
    """Replace every allocation of `anchor` with `allocations`.

    An empty list unlinks the anchor. Raises ValueError when an invariant
    would break; nothing is written in that case.
    """
    if not allocations:
        await _clear_anchor(session, anchor.id)
        return

    reason = await link_block_reason(session, anchor)
    if reason:
        raise ValueError(reason)
    is_counterpart = await session.scalar(
        select(ReimbursementAllocation.id)
        .where(ReimbursementAllocation.counterpart_id == anchor.id)
        .limit(1)
    )
    if is_counterpart is not None:
        raise ValueError("A reimbursement cannot have reimbursements of its own")

    counterpart_ids = [item.counterpart_id for item in allocations]
    if len(set(counterpart_ids)) != len(counterpart_ids):
        raise ValueError("Each reimbursement can appear only once")
    if anchor.id in counterpart_ids:
        raise ValueError("A transaction cannot reimburse itself")

    result = await session.execute(
        select(Transaction).where(
            Transaction.id.in_(counterpart_ids),
            Transaction.workspace_id == anchor.workspace_id,
        )
    )
    counterparts = {tx.id: tx for tx in result.scalars().all()}
    if len(counterparts) != len(counterpart_ids):
        raise ValueError("Transaction not found")

    counterpart_anchors = await session.execute(
        select(ReimbursementAllocation.anchor_id)
        .where(ReimbursementAllocation.anchor_id.in_(counterpart_ids))
        .limit(1)
    )
    if counterpart_anchors.first() is not None:
        raise ValueError("A transaction with reimbursements of its own cannot be a reimbursement")

    already = await allocated_from(session, counterpart_ids, excluding_anchor_id=anchor.id)
    resolved: list[tuple[uuid.UUID, Decimal]] = []
    for item in allocations:
        counterpart = counterparts[item.counterpart_id]
        if counterpart.type == anchor.type:
            raise ValueError("A reimbursement must go in the opposite direction")
        reason = await link_block_reason(session, counterpart)
        if reason:
            raise ValueError(reason)
        amount = _quantize(Decimal(str(item.amount)))
        if amount <= 0:
            raise ValueError("Each reimbursement amount must be positive")
        if already.get(counterpart.id, _ZERO) + amount > _magnitude(counterpart):
            raise ValueError("A reimbursement cannot be allocated beyond its amount")
        resolved.append((counterpart.id, amount))

    await _clear_anchor(session, anchor.id)
    for counterpart_id, amount in resolved:
        session.add(
            ReimbursementAllocation(
                workspace_id=anchor.workspace_id,
                anchor_id=anchor.id,
                counterpart_id=counterpart_id,
                amount=amount,
            )
        )
    await session.flush()


async def _clear_anchor(session: AsyncSession, anchor_id: uuid.UUID) -> None:
    await session.execute(
        delete(ReimbursementAllocation).where(ReimbursementAllocation.anchor_id == anchor_id)
    )
    await session.flush()


async def check_edit(
    session: AsyncSession,
    tx: Transaction,
    *,
    new_amount: Optional[Decimal] = None,
    new_type: Optional[str] = None,
    adds_splits: bool = False,
) -> None:
    """Refuse a manual edit that would break the row's links.

    A sync is not refused (the bank's amount wins); reports scale an
    over-allocated counterpart down to fit instead.
    """
    flips = new_type is not None and new_type != tx.type
    if (flips or adds_splits) and await is_linked(session, tx.id):
        if flips:
            raise ValueError("Unlink the reimbursements before changing the direction")
        raise ValueError("Unlink the reimbursements before splitting among people")
    if new_amount is None:
        return
    total = (await allocated_from(session, [tx.id])).get(tx.id, _ZERO)
    if total > abs(Decimal(str(new_amount))):
        raise ValueError("The amount cannot be less than what is allocated out of it")


def _convert(amount: Decimal, source: Transaction, target: Transaction) -> Optional[Decimal]:
    """`amount`, in `source`'s currency, in `target`'s currency at the rates
    stored on the two rows, or None without one."""
    if source.currency.upper() == target.currency.upper():
        return amount
    source_full, target_full = _magnitude(source), _magnitude(target)
    if (
        source.amount_primary is None
        or target.amount_primary is None
        or not source_full
        or not target_full
    ):
        return None
    primary = amount * abs(Decimal(str(source.amount_primary))) / source_full
    target_primary = abs(Decimal(str(target.amount_primary)))
    if not target_primary:
        return None
    return primary * target_full / target_primary


def _linked(tx: Transaction, unallocated: Decimal) -> ReimbursementLinkedTransaction:
    return ReimbursementLinkedTransaction(
        id=tx.id,
        account_id=tx.account_id,
        description=tx.description,
        date=tx.date,
        amount=tx.amount,
        currency=tx.currency,
        type=tx.type,
        unallocated=unallocated,
    )


async def get_reimbursements(
    session: AsyncSession, tx: Transaction
) -> TransactionReimbursementsRead:
    """`tx`'s links from its own side, with what is left of each row."""
    result = await session.execute(
        select(ReimbursementAllocation)
        .where(
            ReimbursementAllocation.workspace_id == tx.workspace_id,
            or_(
                ReimbursementAllocation.anchor_id == tx.id,
                ReimbursementAllocation.counterpart_id == tx.id,
            ),
        )
        .order_by(ReimbursementAllocation.created_at, ReimbursementAllocation.id)
    )
    allocations = list(result.scalars().all())
    other_ids = {
        a.counterpart_id if a.anchor_id == tx.id else a.anchor_id for a in allocations
    }
    others: dict[uuid.UUID, Transaction] = {}
    if other_ids:
        rows = await session.execute(select(Transaction).where(Transaction.id.in_(other_ids)))
        others = {row.id: row for row in rows.scalars().all()}

    counterpart_ids = [a.counterpart_id for a in allocations if a.anchor_id == tx.id]
    totals = await allocated_from(session, [*counterpart_ids, tx.id])

    def remaining(row: Transaction) -> Decimal:
        return _magnitude(row) - totals.get(row.id, _ZERO)

    counterparts: list[ReimbursementAllocationRead] = []
    anchors: list[ReimbursementAllocationRead] = []
    paid_back: Optional[Decimal] = _ZERO
    for allocation in allocations:
        if allocation.anchor_id == tx.id:
            other = others[allocation.counterpart_id]
            counterparts.append(_read(allocation, _linked(other, remaining(other))))
            if paid_back is not None:
                converted = _convert(Decimal(str(allocation.amount)), other, tx)
                paid_back = None if converted is None else paid_back + converted
        else:
            other = others[allocation.anchor_id]
            anchors.append(_read(allocation, _linked(other, remaining(other))))

    own_remaining = remaining(tx)
    net_amount = None
    if counterparts and paid_back is not None:
        net_amount = _quantize(_magnitude(tx) - paid_back)
    return TransactionReimbursementsRead(
        transaction_id=tx.id,
        counterparts=counterparts,
        anchors=anchors,
        unallocated=own_remaining if anchors else _magnitude(tx),
        over_allocated=bool(anchors) and own_remaining < 0,
        net_amount=net_amount,
    )


def _read(
    allocation: ReimbursementAllocation, other: ReimbursementLinkedTransaction
) -> ReimbursementAllocationRead:
    return ReimbursementAllocationRead(
        id=allocation.id,
        anchor_id=allocation.anchor_id,
        counterpart_id=allocation.counterpart_id,
        amount=allocation.amount,
        created_at=allocation.created_at,
        transaction=other,
    )


async def find_candidates(
    session: AsyncSession,
    tx: Transaction,
    *,
    looking_for: Literal["counterparts", "anchors"],
    search: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = 20,
) -> list[ReimbursementLinkedTransaction]:
    """Rows that could be linked to `tx`, nearest in date first.

    `counterparts` are rows that could pay `tx` back, `anchors` rows `tx`
    could pay back. Either way they go in the other direction, sit in the
    same workspace, pass `link_block_reason` and are not already on the side
    that would make a chain. A counterpart with nothing left to allocate is
    left out, unless it is already linked to `tx`. Without a search or a
    date range the window is `SUGGESTION_DAYS` around `tx`.
    """
    from app.models.payee import Payee

    if search is None and from_date is None and to_date is None:
        from_date = tx.date - timedelta(days=SUGGESTION_DAYS)
        to_date = tx.date + timedelta(days=SUGGESTION_DAYS)

    is_anchor = exists().where(ReimbursementAllocation.anchor_id == Transaction.id)
    is_counterpart = exists().where(ReimbursementAllocation.counterpart_id == Transaction.id)
    blocked_category = exists().where(
        Category.id == Transaction.category_id,
        or_(Category.is_ignored.is_(True), Category.treat_as_transfer.is_(True)),
    )
    stmt = (
        select(Transaction)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(
            Transaction.workspace_id == tx.workspace_id,
            Transaction.id != tx.id,
            Transaction.type != tx.type,
            Transaction.transfer_pair_id.is_(None),
            Transaction.is_ignored.is_(False),
            Transaction.exclude_from_pnl.is_(False),
            Transaction.source.not_in(_UNLINKABLE_SOURCES),
            Transaction.amount != 0,
            ~blocked_category,
            ~exists().where(TransactionSplit.transaction_id == Transaction.id),
            ~(is_counterpart if looking_for == "anchors" else is_anchor),
        )
    )
    if from_date is not None:
        stmt = stmt.where(Transaction.date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Transaction.date <= to_date)
    if search:
        term = f"%{search}%"
        stmt = stmt.where(
            or_(
                Transaction.description.ilike(term),
                Transaction.payee.ilike(term),
                Transaction.notes.ilike(term),
                Payee.name.ilike(term),
            )
        )
    # Over-fetch, newest first: the nearest dates are picked below, and
    # counterparts fully allocated elsewhere drop out.
    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id).limit(limit * 3)
    rows = sorted(
        (await session.execute(stmt)).scalars().all(),
        key=lambda row: (abs((row.date - tx.date).days), str(row.id)),
    )
    if looking_for == "anchors":
        return [_linked(row, _magnitude(row)) for row in rows[:limit]]

    linked_to_tx = select(ReimbursementAllocation.counterpart_id).where(
        ReimbursementAllocation.anchor_id == tx.id
    )
    already = set((await session.execute(linked_to_tx)).scalars().all())
    totals = await allocated_from(session, [row.id for row in rows])
    out = []
    for row in rows:
        left = _magnitude(row) - totals.get(row.id, _ZERO)
        if left > 0 or row.id in already:
            out.append(_linked(row, left))
    return out[:limit]
