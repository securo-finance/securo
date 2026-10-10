"""Service for category-split allocations on transactions.

A category allocation splits one transaction across multiple categories so
budgets and reports attribute each portion correctly. This is orthogonal to
the people-split feature (transaction_splits): a transaction can have one or
the other, never both.

Design mirrors split_service.py: validate inputs, materialize amounts,
delete-then-insert replace semantics.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_category_allocation import TransactionCategoryAllocation
from app.schemas.transaction_category_allocation import (
    CategoryAllocationsInput,
)

_CENT = Decimal("0.01")


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _materialize(
    total: Decimal, payload: CategoryAllocationsInput
) -> list[tuple[uuid.UUID, Decimal, Optional[str], int]]:
    """Resolve allocations into [(category_id, amount, notes, position)] tuples.

    `total` is treated as a positive value — sign is independent of distribution.
    The last row absorbs any rounding residual so the sum is exact.
    """
    total = _quantize(Decimal(str(total)).copy_abs())
    rows = payload.allocations
    n = len(rows)

    if n < 2:
        raise ValueError("Category split requires at least 2 parts")

    # Validate category uniqueness
    ids = [r.category_id for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Each category can appear at most once per transaction")

    # Quantize all amounts
    amounts: list[Decimal] = [_quantize(r.amount) for r in rows]

    # Exact-sum check (last row absorbs residual if caller passed exact values)
    running = sum(amounts[:-1], Decimal("0"))
    last = total - running
    # Validate the caller's last amount is reasonably close (within 1 cent)
    # before we override it with the residual
    if abs(amounts[-1] - last) >= _CENT:
        raise ValueError(
            f"Category amounts must sum to the transaction amount "
            f"(got {sum(amounts, Decimal('0'))}, expected {total})"
        )
    amounts[-1] = last  # Assign residual to last row so sum is exact

    return [
        (rows[i].category_id, amounts[i], rows[i].notes, i)
        for i in range(n)
    ]


async def replace_category_allocations(
    session: AsyncSession,
    transaction: Transaction,
    payload: Optional[CategoryAllocationsInput],
    user_id: uuid.UUID,
) -> None:
    """Replace any existing category allocations on `transaction` with the given payload.

    Pass `payload=None` to leave allocations untouched.
    Pass `CategoryAllocationsInput(allocations=[])` to clear them.
    """
    if payload is None:
        return

    # Replace semantics: always delete existing rows first
    await session.execute(
        delete(TransactionCategoryAllocation).where(
            TransactionCategoryAllocation.transaction_id == transaction.id
        )
    )

    if not payload.allocations:
        return

    # Validate every allocation category belongs to the transaction's workspace.
    # The FK only proves the row exists somewhere; it does not enforce cross-workspace isolation.
    allocation_category_ids = [r.category_id for r in payload.allocations]
    existing = (
        await session.execute(
            select(Category.id).where(
                Category.id.in_(allocation_category_ids),
                Category.workspace_id == transaction.workspace_id,
            )
        )
    ).scalars().all()
    found_ids = set(existing)
    missing = [str(cid) for cid in allocation_category_ids if cid not in found_ids]
    if missing:
        raise ValueError(
            f"Category IDs not found in this workspace: {', '.join(missing)}"
        )

    for category_id, amount, notes, position in _materialize(transaction.amount, payload):
        session.add(
            TransactionCategoryAllocation(
                transaction_id=transaction.id,
                workspace_id=transaction.workspace_id,
                category_id=category_id,
                amount=amount,
                notes=notes,
                position=position,
            )
        )
