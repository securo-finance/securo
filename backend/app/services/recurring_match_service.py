"""Match recurring bills to the real transactions that pay them (issue #116).

A recurring bill (rent, a subscription) is charged to a bank account or credit
card, and bank sync then imports that charge as its own transaction. Without
linking, the user ends up with two rows: the bill's generated placeholder and
the synced charge. This service reconciles the two.

Matching signals (the intersection of what Actual and Sure use): same account,
same direction (``type``), exact amount, transaction date within a small window
of the expected occurrence, and description token-similarity above the same bar
the bank-sync fuzzy merge already uses. Matches are one-to-one and only the
high-confidence (exact-amount) tier auto-links; softer/variable-amount matching
is intentionally left for a later suggestion-based pass.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction

# Description-similarity bar for accepting a link. Matches the established
# bank-sync fuzzy-merge threshold (connection_service._fuzzy_match_manual).
_SIMILARITY_THRESHOLD = 0.6

# Real-transaction sources a recurring charge can arrive under. Excludes
# "recurring" itself — that is the generated placeholder, handled separately.
_REAL_SOURCES = ("sync", "ofx", "csv", "manual")


def _description_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Token-overlap ratio between two descriptions (0..1)."""
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


def _match_window(frequency: str) -> tuple[int, int]:
    """Days (before, after) an expected occurrence a real charge may land.

    Charges commonly post a few days after the nominal due date and sometimes
    a day or two early. Windows stay well under one period so a charge can
    never match the neighbouring occurrence.
    """
    if frequency == "weekly":
        return 2, 2
    # monthly / yearly
    return 3, 5


def _best_by_similarity(
    candidates, description: Optional[str]
) -> Optional[Transaction]:
    best: Optional[Transaction] = None
    best_score = 0.0
    for cand in candidates:
        if cand.is_ignored:
            continue
        score = _description_similarity(cand.description, description)
        if score > best_score:
            best_score = score
            best = cand
    return best if best and best_score >= _SIMILARITY_THRESHOLD else None


async def find_real_tx_for_occurrence(
    session: AsyncSession,
    recurring: RecurringTransaction,
    occurrence_date: date,
) -> Optional[Transaction]:
    """Find an unlinked real transaction that fulfills this bill's occurrence.

    Used by generate_pending: instead of writing a duplicate placeholder for an
    occurrence a real charge already covers, link that charge to the bill.
    """
    before, after = _match_window(recurring.frequency)
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == recurring.account_id,
            Transaction.recurring_transaction_id.is_(None),
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.amount == recurring.amount,
            Transaction.currency == recurring.currency,
            Transaction.type == recurring.type,
            Transaction.date >= occurrence_date - timedelta(days=before),
            Transaction.date <= occurrence_date + timedelta(days=after),
        )
    )
    return _best_by_similarity(result.scalars(), recurring.description)


async def find_placeholder_for_incoming(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    tx_type: str,
    tx_date: date,
    description: Optional[str],
) -> Optional[Transaction]:
    """Find an unmatched generated placeholder this incoming charge fulfills.

    Placeholders carry ``source="recurring"`` and a ``recurring_transaction_id``
    but no ``external_id`` yet. The caller upgrades the matched row in place to
    the synced/imported charge, preserving the recurring link (no duplicate).
    """
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source == "recurring",
            Transaction.recurring_transaction_id.is_not(None),
            Transaction.external_id.is_(None),
            Transaction.amount == amount,
            Transaction.currency == currency,
            Transaction.type == tx_type,
            Transaction.date >= tx_date - timedelta(days=5),
            Transaction.date <= tx_date + timedelta(days=5),
        )
    )
    return _best_by_similarity(result.scalars(), description)


async def find_bill_for_incoming(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    tx_type: str,
    tx_date: date,
    description: Optional[str],
) -> Optional[RecurringTransaction]:
    """Find an active bill whose next expected occurrence this charge fulfills.

    Used when a real charge arrives before any placeholder was generated. The
    caller stamps the charge with the bill and advances the bill past the
    fulfilled occurrence so generate_pending won't later duplicate it.
    """
    result = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.account_id == account_id,
            RecurringTransaction.is_active.is_(True),
            RecurringTransaction.amount == amount,
            RecurringTransaction.currency == currency,
            RecurringTransaction.type == tx_type,
        )
    )
    best: Optional[RecurringTransaction] = None
    best_score = 0.0
    for rec in result.scalars():
        before, after = _match_window(rec.frequency)
        lo = rec.next_occurrence - timedelta(days=before)
        hi = rec.next_occurrence + timedelta(days=after)
        if not (lo <= tx_date <= hi):
            continue
        score = _description_similarity(rec.description, description)
        if score > best_score:
            best_score = score
            best = rec
    return best if best and best_score >= _SIMILARITY_THRESHOLD else None


def advance_past(recurring: RecurringTransaction, fulfilled_date: date) -> None:
    """Advance a bill's next_occurrence past the occurrence a charge fulfilled.

    The target is floored at the bill's current next_occurrence — the matched
    occurrence — so an *early-posted* charge (one that lands inside the
    before-window, i.e. before next_occurrence) still moves the pointer forward.
    Advancing only past the posting date would leave next_occurrence unchanged
    for early charges (e.g. a Jan 8 charge for a Jan 10 occurrence), and
    generate_pending would then re-create that occurrence as a duplicate.

    Deactivates the bill if it advances beyond its end_date. Lazy-imports the
    date helper to avoid a circular import with recurring_transaction_service.
    """
    from app.services.recurring_transaction_service import _advance_date

    intended_day = recurring.day_of_month or recurring.start_date.day
    target = max(fulfilled_date, recurring.next_occurrence)
    guard = 0
    while recurring.next_occurrence <= target and guard < 500:
        recurring.next_occurrence = _advance_date(
            recurring.next_occurrence, recurring.frequency, intended_day=intended_day
        )
        guard += 1
    if recurring.end_date and recurring.next_occurrence > recurring.end_date:
        recurring.is_active = False
