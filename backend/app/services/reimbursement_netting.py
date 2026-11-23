"""How reimbursement allocations change user-level P&L (ADR 0031).

An allocation moves money out of two totals at once. The counterpart's
allocated part leaves its own period, category and direction: the friend's
payback is not income. The same amount comes off the anchor, in the anchor's
period, category and direction: only the user's share of the dinner is an
expense. Once an anchor is paid back more than in full, the excess counts in
the opposite direction, still in the anchor's category: being paid 150 back
for a 100 gift is 50 of income, not -50 of spending.

Like the group-split offsets in `_query_filters`, this works on the side of
the full-row totals: callers sum rows as usual and subtract the parts
returned here. A part's `amount` is positive when it comes off its
direction's total and negative when it adds to it.

An allocation only counts while both of its rows count toward user P&L, and
neither is split among people (that rule nets the row already). A
counterpart a sync shrank below its allocations has them scaled down to fit,
so a payback never takes off more than it brought in.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.models.reimbursement_allocation import ReimbursementAllocation
from app.models.transaction import Transaction

_ZERO = Decimal("0")
_OPPOSITE = {"debit": "credit", "credit": "debit"}


@dataclass(frozen=True)
class NettingPart:
    transaction_id: uuid.UUID
    # The row's reporting date, or the caller's period label for it.
    label: Any
    category_id: Optional[uuid.UUID]
    # The direction whose total the part comes off.
    type: str
    amount: Decimal


def _counted_ids():
    """Rows an allocation may net: they count toward user P&L and are not
    split among people. Never correlated, so it can sit inside a query over
    `transactions`."""
    from app.models.transaction_split import TransactionSplit
    from app.services._query_filters import counts_as_user_pnl_row

    return (
        select(Transaction.id)
        .where(
            counts_as_user_pnl_row(),
            ~exists().where(TransactionSplit.transaction_id == Transaction.id),
        )
        .correlate(None)
    )


def _live_allocations(workspace_id: Optional[uuid.UUID]):
    """Filters for the allocations that count, see `_counted_ids`."""
    filters = [
        ReimbursementAllocation.anchor_id.in_(_counted_ids()),
        ReimbursementAllocation.counterpart_id.in_(_counted_ids()),
    ]
    if workspace_id is not None:
        filters.append(ReimbursementAllocation.workspace_id == workspace_id)
    return filters


def on_a_live_link(workspace_id: Optional[uuid.UUID] = None):
    """SQL filter: the row is on either side of an allocation that counts."""
    live = _live_allocations(workspace_id)
    return or_(
        Transaction.id.in_(select(ReimbursementAllocation.anchor_id).where(*live).correlate(None)),
        Transaction.id.in_(select(ReimbursementAllocation.counterpart_id).where(*live).correlate(None)),
    )


def _magnitude(tx: Transaction) -> Decimal:
    return abs(Decimal(str(tx.amount or 0)))


async def _effective_amounts(
    session: AsyncSession,
    allocations: list[ReimbursementAllocation],
) -> dict[uuid.UUID, Decimal]:
    """Each allocation's amount, scaled down when its counterpart's
    allocations add up to more than the counterpart (a sync shrank it)."""
    from app.services.reimbursement_service import allocated_from

    counterpart_ids = {a.counterpart_id for a in allocations}
    totals = await allocated_from(session, counterpart_ids)
    rows = await session.execute(
        select(Transaction.id, Transaction.amount).where(Transaction.id.in_(counterpart_ids))
    )
    full = {tx_id: abs(Decimal(str(amount or 0))) for tx_id, amount in rows.all()}
    out: dict[uuid.UUID, Decimal] = {}
    for allocation in allocations:
        amount = Decimal(str(allocation.amount))
        total = totals.get(allocation.counterpart_id, _ZERO)
        cap = full.get(allocation.counterpart_id, _ZERO)
        if total > cap and total:
            amount = amount * cap / total
        out[allocation.id] = amount
    return out


def _spread(
    tx: Transaction, label: Any, type_: str, amount: Decimal
) -> list[NettingPart]:
    """`amount` over the parts of `tx` that count, in proportion."""
    from app.services._query_filters import user_pnl_parts

    if not amount:
        return []
    parts = user_pnl_parts(tx)
    weight = sum((ratio for _, _, ratio in parts), _ZERO)
    if not weight:
        return []
    return [
        NettingPart(tx.id, label, category_id, type_, amount * ratio / weight)
        for category_id, _, ratio in parts
    ]


async def _in_primary(
    session: AsyncSession,
    tx: Transaction,
    amount: Decimal,
    primary_currency: Optional[str],
    rates: dict,
) -> Optional[Decimal]:
    """`amount`, in `tx`'s currency, at the rate the row's own totals use."""
    from app.services._query_filters import _others_share_in_primary

    row = (tx.id, tx.category_id, tx.type, tx.currency, tx.amount, tx.amount_primary, tx.date, amount)
    return await _others_share_in_primary(session, row, primary_currency, rates)


async def live_allocations_for(
    session: AsyncSession,
    transaction_ids: Iterable[uuid.UUID],
    workspace_id: uuid.UUID,
) -> list[tuple[ReimbursementAllocation, Decimal]]:
    """The allocations that count and touch any of these rows, each with its
    amount in the counterpart's currency (scaled down when a sync shrank the
    counterpart below its allocations, see `_effective_amounts`)."""
    ids = list(transaction_ids)
    if not ids:
        return []
    result = await session.execute(
        select(ReimbursementAllocation).where(
            *_live_allocations(workspace_id),
            or_(
                ReimbursementAllocation.anchor_id.in_(ids),
                ReimbursementAllocation.counterpart_id.in_(ids),
            ),
        )
    )
    allocations = list(result.scalars().all())
    effective = await _effective_amounts(session, allocations)
    return [(allocation, effective[allocation.id]) for allocation in allocations]


async def reimbursement_offset_parts(
    session: AsyncSession,
    user_id: uuid.UUID,
    start: date,
    end: date,
    *,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
    workspace_id: Optional[uuid.UUID] = None,
    end_inclusive: bool = False,
    label_expr=None,
    extra_filters: Iterable = (),
    cutoff: Optional[date] = None,
) -> list[NettingPart]:
    """The parts to take off the user's posted P&L between `start` and `end`.

    Each side lands in its own row's period: a counterpart's part on the
    counterpart's reporting date, an anchor's on the anchor's. Rows are
    picked like the totals they come off (posted, dated up to today, never an
    opening balance, every member's rows in the workspace), so the parts come
    off the same totals. Without `primary_currency` amounts stay in each
    row's own currency; with it they are converted at the rate stored on
    the counterpart, and an allocation with no rate at all is left out.
    """
    from app.services._query_filters import reporting_date_col

    date_col = reporting_date_col("accrual" if use_effective_date else "cash")
    label = label_expr if label_expr is not None else date_col
    filters = [
        Transaction.source != "opening_balance",
        Transaction.status == "posted",
        date_col >= start,
        date_col <= end if end_inclusive else date_col < end,
        # Posted rows up to `cutoff`, today unless the report counts further.
        date_col <= (cutoff if cutoff is not None else app_today()),
        *extra_filters,
    ]
    # Links belong to the workspace, and the totals they come off count every
    # member's rows, so the workspace is the scope. The user only narrows it
    # when no workspace is given.
    filters.append(
        Transaction.workspace_id == workspace_id
        if workspace_id is not None
        else Transaction.user_id == user_id
    )
    live = _live_allocations(workspace_id)

    async def side_rows(column) -> dict[uuid.UUID, tuple[Transaction, Any]]:
        linked = select(column).where(*live).correlate(None)
        result = await session.execute(
            select(Transaction, label)
            .options(selectinload(Transaction.category))
            .where(Transaction.id.in_(linked), *filters)
        )
        return {tx.id: (tx, row_label) for tx, row_label in result.all()}

    anchors = await side_rows(ReimbursementAllocation.anchor_id)
    counterparts = await side_rows(ReimbursementAllocation.counterpart_id)
    if not anchors and not counterparts:
        return []

    result = await session.execute(
        select(ReimbursementAllocation).where(
            *live,
            or_(
                ReimbursementAllocation.anchor_id.in_(list(anchors)),
                ReimbursementAllocation.counterpart_id.in_(list(counterparts)),
            ),
        )
    )
    allocations = list(result.scalars().all())
    effective = await _effective_amounts(session, allocations)

    # Every row on either end, with its category loaded: a row's counted share
    # (the ratios of the parts that count toward P&L) is below 1 when part of
    # it sits in an ignored or transfer-like category.
    from app.services._query_filters import user_pnl_parts

    loaded = {tx_id: tx for tx_id, (tx, _) in {**anchors, **counterparts}.items()}
    missing = (
        {a.anchor_id for a in allocations} | {a.counterpart_id for a in allocations}
    ) - set(loaded)
    if missing:
        rows = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(Transaction.id.in_(missing))
        )
        loaded.update({tx.id: tx for tx in rows.scalars().all()})
    counted_share = {
        tx_id: sum((ratio for _, _, ratio in user_pnl_parts(tx)), _ZERO)
        for tx_id, tx in loaded.items()
    }

    # Converted at the counterpart's rate on both sides, so the two parts of an
    # allocation are the same figure, and no more than either row counts: each
    # side gives up only its counted share, never more than it has.
    rates: dict = {}
    converted: dict[uuid.UUID, Decimal] = {}
    for allocation in allocations:
        value = await _in_primary(
            session, loaded[allocation.counterpart_id], effective[allocation.id],
            primary_currency, rates,
        )
        if value is not None:
            converted[allocation.id] = value * min(
                counted_share[allocation.anchor_id], counted_share[allocation.counterpart_id]
            )

    parts: list[NettingPart] = []
    for tx_id, (tx, row_label) in counterparts.items():
        total = sum(
            (converted[a.id] for a in allocations if a.counterpart_id == tx_id and a.id in converted),
            _ZERO,
        )
        parts.extend(_spread(tx, row_label, tx.type, total))
    for tx_id, (tx, row_label) in anchors.items():
        total = sum(
            (converted[a.id] for a in allocations if a.anchor_id == tx_id and a.id in converted),
            _ZERO,
        )
        full = (
            abs(Decimal(str(tx.amount_primary)))
            if primary_currency is not None and tx.amount_primary is not None
            else _magnitude(tx)
        )
        # What the anchor counts, not its whole amount: the excess beyond it
        # flips to the other direction.
        reduced = min(total, full * counted_share[tx_id])
        parts.extend(_spread(tx, row_label, tx.type, reduced))
        parts.extend(_spread(tx, row_label, _OPPOSITE[tx.type], -(total - reduced)))
    return parts


async def reimbursement_share_fractions(
    session: AsyncSession,
    transactions: Iterable[Transaction],
    *,
    clamp: bool = False,
    in_primary: bool = False,
) -> dict[uuid.UUID, Decimal]:
    """Map forecast rows that are a side of an allocation to the fraction of
    them that still counts, for rows the posted parts above never see
    (pending or future-dated ones).

    Signed by default: an anchor paid back more than in full gets a negative
    fraction, its excess, which is what a balance curve needs (its delta is
    the row's amount times the fraction). The P&L forecast puts a row's
    share in the row's own direction, where a negative share would read as a
    negative expense, so it asks for `clamp`: the share stops at zero, and
    the excess of a pending or future anchor is not projected as income the
    way the posted path counts it. The fraction is in the row's own
    currency, at the rates stored on the rows.

    The P&L forecast multiplies a share into `amount_primary`, and the posted
    path takes the counterpart's part off at the counterpart's own primary
    value, which differs from the anchor's when the two were booked at
    different rates. `in_primary` measures an anchor's share the same way
    (its primary amount less what the counterparts' parts are worth in
    primary), and keeps the native share for a row or link with no primary
    amount. A balance curve works in the account's currency and leaves it off.
    """
    from app.services.reimbursement_service import _convert

    rows = {tx.id: tx for tx in transactions}
    if not rows:
        return {}
    result = await session.execute(
        select(ReimbursementAllocation).where(
            *_live_allocations(None),
            or_(
                ReimbursementAllocation.anchor_id.in_(list(rows)),
                ReimbursementAllocation.counterpart_id.in_(list(rows)),
            ),
        )
    )
    allocations = list(result.scalars().all())
    if not allocations:
        return {}
    effective = await _effective_amounts(session, allocations)
    others = (
        {a.anchor_id for a in allocations} | {a.counterpart_id for a in allocations}
    ) - set(rows)
    every = dict(rows)
    if others:
        loaded = await session.execute(select(Transaction).where(Transaction.id.in_(others)))
        every.update({tx.id: tx for tx in loaded.scalars().all()})

    taken: dict[uuid.UUID, Decimal] = {}
    in_primary_value: dict[uuid.UUID, Decimal] = {}
    primary_usable: dict[uuid.UUID, bool] = {}
    for allocation in allocations:
        amount = effective[allocation.id]
        if allocation.counterpart_id in rows:
            taken[allocation.counterpart_id] = taken.get(allocation.counterpart_id, _ZERO) + amount
        if allocation.anchor_id in rows:
            counterpart, anchor = every[allocation.counterpart_id], every[allocation.anchor_id]
            value = _convert(amount, counterpart, anchor)
            if value is not None:
                taken[allocation.anchor_id] = taken.get(allocation.anchor_id, _ZERO) + value
            if in_primary:
                usable = (
                    anchor.amount_primary is not None
                    and counterpart.amount_primary is not None
                    and bool(_magnitude(counterpart))
                )
                primary_usable[anchor.id] = primary_usable.get(anchor.id, True) and usable
                if usable:
                    in_primary_value[anchor.id] = in_primary_value.get(anchor.id, _ZERO) + (
                        amount * abs(Decimal(str(counterpart.amount_primary))) / _magnitude(counterpart)
                    )

    fractions: dict[uuid.UUID, Decimal] = {}
    for tx_id, amount in taken.items():
        full = _magnitude(rows[tx_id])
        anchor_primary = rows[tx_id].amount_primary
        if in_primary and primary_usable.get(tx_id) and anchor_primary is not None and anchor_primary:
            amount, full = in_primary_value[tx_id], abs(Decimal(str(anchor_primary)))
        if full:
            share = 1 - amount / full
            fractions[tx_id] = max(share, _ZERO) if clamp else share
    return fractions
