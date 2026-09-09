"""What is still owed, as money the forecast can carry.

## Why invoices were kept out until now

Everything that projects in Securo does so **by being a transaction**: a
row counts as forecast when it is `pending`, or `posted` with a future
date, and a recurring schedule enters by being materialised into rows.
An invoice is a claim, not a movement, so it never qualified for free.

Projecting it before matching existed would have **double counted**. The
same R$5.000 is routinely an open invoice *and* a pending bank credit,
and until the two could be said to be the same money, adding both would
have inflated every forecast in the product. Saying they are the same
money is exactly what payment matching does, which is why this lands
with it and not before.

## What resolves the double count

Only the **unallocated** balance is projected. The moment a payment is
linked, the invoice's outstanding drops and the transaction carries the
forecast alone, so the total never moves twice for one payment. Nothing
has to be kept in sync: the subtraction is the mechanism.

## Three deliberate narrowings

**Only `status == 'open'`.** That is the stored decision, not the derived
reading. A draft is not owed by anybody yet, and `void` and
`uncollectible` are the two ways of saying the money is not coming.

**Only inside the window, by due date.** An invoice sixty days overdue is
still owed, and it is deliberately *not* projected: the only date we have
for it has passed, and moving it to today would be inventing a date
nobody promised. Overdue money is the aging table's subject, and a
forecast that quietly relocates it would make the aging table and the
forecast disagree about the same debt.

**Per currency, unconverted.** The caller decides. The dashboard keeps a
figure per currency, so a euro invoice belongs in the euro bucket; the
cash-flow report sums into one currency and converts on the way in.
Converting here would force the first caller to undo it.

## The module gate

There is no flag lookup. A workspace without invoicing has no rows in
`invoices`, so this costs one index probe and returns nothing, which is
cheaper than the query that would have asked whether to run it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceAllocation

ZERO = Decimal("0")


@dataclass(frozen=True)
class Claim:
    """One promise of money, and when it was promised for."""

    due_date: date
    currency: str
    #: Always positive. `direction` says which way it points, because the
    #: two callers sign it differently: a balance walk adds or subtracts,
    #: a cash-flow report files it under income or expenses.
    amount: Decimal
    #: `receivable` (money owed to the workspace) or `payable`.
    direction: str

    @property
    def signed(self) -> Decimal:
        return self.amount if self.direction == "receivable" else -self.amount


async def claims_in_range(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    range_start: date,
    range_end: date,
    *,
    directions: Optional[tuple[str, ...]] = None,
) -> list[Claim]:
    """Every open invoice falling due in `[range_start, range_end)`.

    Half-open on purpose, matching `_get_forecast_transactions`, so a
    caller walking month by month never counts the boundary day twice.
    """
    allocated = (
        select(
            InvoiceAllocation.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceAllocation.amount), 0).label("total"),
        )
        .group_by(InvoiceAllocation.invoice_id)
        .subquery()
    )

    query = (
        select(
            Invoice.due_date,
            Invoice.currency,
            Invoice.direction,
            (Invoice.total - func.coalesce(allocated.c.total, 0)).label("outstanding"),
        )
        .outerjoin(allocated, allocated.c.invoice_id == Invoice.id)
        .where(
            Invoice.workspace_id == workspace_id,
            Invoice.status == "open",
            Invoice.due_date >= range_start,
            Invoice.due_date < range_end,
            (Invoice.total - func.coalesce(allocated.c.total, 0)) > 0,
        )
    )

    if directions is not None:
        query = query.where(Invoice.direction.in_(directions))

    rows = (await session.execute(query)).all()
    return [
        Claim(
            due_date=row.due_date,
            currency=row.currency or "USD",
            amount=Decimal(str(row.outstanding)),
            direction=row.direction,
        )
        for row in rows
    ]
