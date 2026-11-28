import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ReimbursementAllocationInput(BaseModel):
    """One counterpart paying back part of the anchor. `amount` is the part
    of the counterpart that goes to this anchor, in the counterpart's own
    currency."""

    counterpart_id: uuid.UUID
    # Cents, what the column stores: anything finer would validate against
    # one remainder and be saved as another. `max_digits` matches
    # Numeric(15, 2), so an absurd value is refused here instead of failing
    # inside the service.
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class ReimbursementAllocationsInput(BaseModel):
    """Every allocation of an anchor, replacing whatever it had. An empty
    list unlinks all its counterparts."""

    allocations: list[ReimbursementAllocationInput]


class ReimbursementLinkedTransaction(BaseModel):
    """The row on the other side of an allocation, enough to label it."""

    id: uuid.UUID
    account_id: uuid.UUID
    description: str
    date: date
    amount: Decimal
    currency: str
    type: str
    # What is left of the row once all its allocations are taken off, in its
    # own currency. Only meaningful for a counterpart.
    unallocated: Decimal


class ReimbursementAllocationRead(BaseModel):
    id: uuid.UUID
    anchor_id: uuid.UUID
    counterpart_id: uuid.UUID
    amount: Decimal
    created_at: datetime
    transaction: ReimbursementLinkedTransaction


class TransactionReimbursementsRead(BaseModel):
    """A transaction's reimbursement links, from its own side.

    An anchor lists its counterparts in `counterparts`; a counterpart lists
    the anchors it pays back in `anchors`. A row is never both.
    """

    transaction_id: uuid.UUID
    counterparts: list[ReimbursementAllocationRead]
    anchors: list[ReimbursementAllocationRead]
    # For a counterpart: its amount less everything allocated out of it.
    unallocated: Decimal
    # A counterpart whose allocations add up to more than its amount, which
    # only a sync can cause; reports scale its allocations down to fit.
    over_allocated: bool
    # For an anchor: its amount less what its counterparts pay back, in its
    # own currency, signed with the anchor's direction (negative once it is
    # paid back more than in full). None while a counterpart in another
    # currency has no conversion.
    net_amount: Optional[Decimal] = None
