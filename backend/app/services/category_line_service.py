"""Splitting one transaction across several categories.

The transaction keeps its single row and full amount (it is what the bank
reports); `transaction_category_lines` says how that amount is distributed.
This module is the only writer of those lines and of
`Transaction.has_category_lines`, and it keeps four invariants:

- a split transaction has at least two lines,
- the lines add up to the transaction amount exactly (leftover cents go to
  the largest fractions, see `_distribute`),
- while split, the transaction's own `category_id` is null,
- every line's category belongs to the transaction's workspace and is
  neither ignored nor hidden. Ignored categories also take a row out of the
  account balance, which a balance computed on the whole row cannot do for
  part of it, so they are refused here rather than half-supported.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.mortgage_payment_allocation import (
    MORTGAGE_PRINCIPAL_SOURCE,
    MortgagePaymentAllocation,
)
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.transaction_split import TransactionSplit
from app.schemas.transaction_category_line import TransactionCategoryLinesInput

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")

# Rows whose amount is owned by something other than the user, or that are
# bookkeeping rather than spending: splitting them would either be undone by
# the next sync or describe money that was never earned or spent.
_UNSPLITTABLE_SOURCES = frozenset({"opening_balance", "settlement", MORTGAGE_PRINCIPAL_SOURCE})


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def materialize(
    total: Decimal, payload: TransactionCategoryLinesInput
) -> list[tuple[uuid.UUID, Decimal, Optional[Decimal], Optional[str]]]:
    """Resolve a payload into [(category_id, amount, share_pct, notes)].

    `total` is taken as a positive value: a line follows the direction of its
    transaction. Raises ValueError when the parts do not add up.
    """
    total = _quantize(Decimal(str(total)).copy_abs())
    lines = payload.lines
    if len(lines) < 2:
        raise ValueError("A category split needs at least two lines")

    if payload.share_type == "exact":
        out = []
        for line in lines:
            if line.amount is None:
                raise ValueError("Each line needs an amount")
            amount = _quantize(Decimal(str(line.amount)))
            if amount <= 0:
                raise ValueError("Each line amount must be positive")
            out.append((line.category_id, amount, None, line.notes))
        if sum((a for _, a, _, _ in out), Decimal("0")) != total:
            raise ValueError("The lines must add up to the transaction amount")
        return out

    if payload.share_type == "percent":
        for line in lines:
            if line.share_pct is None:
                raise ValueError("Each line needs a percentage")
            if line.share_pct <= 0:
                raise ValueError("Each line percentage must be positive")
        if sum((line.share_pct or Decimal("0") for line in lines), Decimal("0")) != _HUNDRED:
            raise ValueError("The line percentages must add up to 100")
        amounts = _distribute(total, [line.share_pct or Decimal("0") for line in lines])
        return [
            (line.category_id, amounts[i], line.share_pct, line.notes)
            for i, line in enumerate(lines)
        ]

    raise ValueError(f"Unknown share_type: {payload.share_type}")


def _distribute(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` by `weights` in whole cents, exactly.

    Largest remainder: every part gets the cents its share rounds down to,
    and the cents left over go one each to the parts with the largest
    fractions (the later part on a tie, so an even split puts the odd cent
    last). Rounding each part on its own could hand the last one nothing
    even when a positive split exists: 4 cents at 37.5/37.5/25 is 1/2/1.
    """
    total_cents = int((total * 100).to_integral_value())
    weight_sum = sum(weights, Decimal("0"))
    exact = [Decimal(total_cents) * weight / weight_sum for weight in weights]
    cents = [int(share) for share in exact]  # floor: shares are positive
    leftover = total_cents - sum(cents)
    by_fraction = sorted(
        range(len(weights)), key=lambda i: (exact[i] - cents[i], i), reverse=True
    )
    for i in by_fraction[:leftover]:
        cents[i] += 1
    if any(c <= 0 for c in cents):
        raise ValueError("The transaction amount is too small to split this way")
    return [Decimal(c) / 100 for c in cents]


async def split_block_reason(session: AsyncSession, tx: Transaction) -> Optional[str]:
    """Why `tx` cannot be split across categories, or None when it can."""
    if tx.transfer_pair_id is not None:
        return "A transfer cannot be split across categories"
    if tx.is_ignored:
        return "An ignored transaction cannot be split across categories"
    if tx.source in _UNSPLITTABLE_SOURCES:
        return "This transaction cannot be split across categories"
    if tx.external_id and tx.external_id.startswith("bill_charge:"):
        # The provider rewrites these rows' amount on every sync.
        return "A card statement charge cannot be split across categories"
    if not tx.amount or Decimal(str(tx.amount)) == 0:
        return "A transaction with no amount cannot be split across categories"
    has_group_split = await session.scalar(
        select(TransactionSplit.id).where(TransactionSplit.transaction_id == tx.id).limit(1)
    )
    if has_group_split is not None:
        return "Remove the split among people before splitting across categories"
    has_mortgage_breakdown = await session.scalar(
        select(MortgagePaymentAllocation.id)
        .where(MortgagePaymentAllocation.payment_transaction_id == tx.id)
        .limit(1)
    )
    if has_mortgage_breakdown is not None:
        return "Remove the mortgage breakdown before splitting across categories"
    return None


async def _load_categories(
    session: AsyncSession, workspace_id: uuid.UUID, category_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Category]:
    result = await session.execute(
        select(Category).where(
            Category.id.in_(set(category_ids)),
            Category.workspace_id == workspace_id,
        )
    )
    categories = {category.id: category for category in result.scalars().all()}
    if len(categories) != len(set(category_ids)):
        raise ValueError("Category not found")
    return categories


async def _validate_line_categories(
    session: AsyncSession, tx: Transaction, categories: dict[uuid.UUID, Category]
) -> None:
    for category in categories.values():
        if category.is_ignored:
            raise ValueError("An ignored category cannot be used in a category split")
        if category.is_hidden:
            raise ValueError("A hidden category cannot be used in a category split")
    await _check_card_credit(session, tx, categories.values())


async def _check_card_credit(session: AsyncSession, tx: Transaction, categories) -> None:
    """A card statement drops a credit in a transfer-like category as a whole
    (an unpaired card payment), and cannot drop only part of one. Refuse the
    combination rather than leave the bill counting it."""
    if tx.type != "credit" or not any(category.treat_as_transfer for category in categories):
        return
    account_type = await session.scalar(select(Account.type).where(Account.id == tx.account_id))
    if account_type == "credit_card":
        raise ValueError(
            "A credit on a credit card cannot have a line in a transfer category"
        )


async def validate_existing_lines(session: AsyncSession, tx: Transaction) -> None:
    """Re-check a split after the transaction's type or account changed:
    a debit that becomes a card credit may now carry a transfer line the
    statement cannot drop."""
    await _ensure_lines_loaded(session, tx)
    if not tx.category_lines:
        return
    categories = await _load_categories(
        session, tx.workspace_id, [line.category_id for line in tx.category_lines]
    )
    await _check_card_credit(session, tx, categories.values())


async def _ensure_lines_loaded(session: AsyncSession, tx: Transaction) -> None:
    # Load (or reload) the collection explicitly: a row created in this
    # session has never loaded it, and touching an unloaded collection under
    # asyncio raises instead of querying.
    await session.refresh(tx, ["category_lines"])


async def replace_lines(
    session: AsyncSession,
    tx: Transaction,
    payload: Optional[TransactionCategoryLinesInput],
) -> None:
    """Replace `tx`'s category split with `payload`.

    `None` leaves the lines alone. An empty `lines` list undoes the split and
    gives the transaction back the category of its largest line (the first one
    on a tie). Two or more lines split it. `tx` must already be flushed, with
    its final amount set.
    """
    if payload is None:
        return
    await _ensure_lines_loaded(session, tx)

    if not payload.lines:
        if tx.has_category_lines and tx.category_lines:
            largest = max(tx.category_lines, key=lambda line: (line.amount, -line.position))
            tx.category_id = largest.category_id
        await _clear(session, tx)
        return

    reason = await split_block_reason(session, tx)
    if reason:
        raise ValueError(reason)
    resolved = materialize(tx.amount, payload)
    categories = await _load_categories(session, tx.workspace_id, [c for c, _, _, _ in resolved])
    await _validate_line_categories(session, tx, categories)

    await _clear(session, tx)
    for position, (category_id, amount, share_pct, notes) in enumerate(resolved):
        tx.category_lines.append(
            TransactionCategoryLine(
                workspace_id=tx.workspace_id,
                category_id=category_id,
                amount=amount,
                share_type=payload.share_type,
                share_pct=share_pct,
                position=position,
                notes=notes,
            )
        )
    tx.category_id = None
    tx.has_category_lines = True
    await session.flush()


async def _clear(session: AsyncSession, tx: Transaction) -> None:
    # Delete the old lines in their own flush: replacing them in one flush
    # would insert the new rows before deleting the old ones and collide on
    # (transaction_id, position).
    tx.category_lines.clear()
    tx.has_category_lines = False
    await session.flush()


async def rescale_lines(session: AsyncSession, tx: Transaction, old_amount: Decimal) -> None:
    """Keep a split consistent after something other than the user changed
    the transaction amount (a recurring placeholder confirmed by the bank for
    a different amount, for instance).

    Percent lines get their percentages back; exact lines keep their
    proportions, in whole cents (see `_distribute`). If the new amount is too
    small to give every line at least a cent, the split is undone instead,
    with the transaction keeping the category of its largest line.
    """
    if not tx.has_category_lines:
        return
    await _ensure_lines_loaded(session, tx)
    lines = list(tx.category_lines)
    new_total = _quantize(Decimal(str(tx.amount)).copy_abs())
    if not lines or new_total == _quantize(Decimal(str(old_amount)).copy_abs()):
        return
    if lines[0].share_type == "percent":
        weights = [line.share_pct or Decimal("0") for line in lines]
    else:
        weights = [line.amount for line in lines]
    try:
        amounts = _distribute(new_total, weights)
    except ValueError:
        largest = max(lines, key=lambda line: (line.amount, -line.position))
        tx.category_id = largest.category_id
        await _clear(session, tx)
        return
    for line, amount in zip(lines, amounts):
        line.amount = amount
    await session.flush()

