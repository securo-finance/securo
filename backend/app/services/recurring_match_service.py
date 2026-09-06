"""Match recurring bills to the real transactions that pay them (issue #116).

A recurring bill (rent, a subscription) is charged to a bank account or credit
card, and bank sync then imports that charge as its own transaction. Without
linking, the user ends up with two rows: the bill's generated placeholder and
the synced charge. This service reconciles the two.

## What changed, and what did not

The judgement used to live here as constants and a hand-rolled comparison.
It now lives in `reconciliation_engine`, under policy documents in
`reconciliation_policy`; the same decider an invoice goes through, because
**an invoice and a scheduled occurrence are the same kind of promise**: money
that is expected, waiting for the movement that confirms it.

**The decisions are unchanged, deliberately.** Same account, same direction,
exact amount, three days before and five after (two and two for a weekly bill,
five either side against a placeholder), description token-overlap at 0.6, and
the better-matching candidate wins rather than both being refused. This lands
under bills that have been reconciling this way since #116, and a matcher that
started finding pairs it used to miss would be a behaviour change nobody asked
for. What is gained today is only that those numbers are now reachable: a
threshold buried in a module is a threshold no one can ever be shown or offered.

The queries stay here, because they are what differs: three lookups, in two
directions, over two tables. Only the verdict is shared.

One dormant edge is worth naming: a bill of zero is now never matched, because
a promise with nothing outstanding is not a candidate. Nothing reaches it:
banks do not post charges of zero, but it is a difference rather than an
identity.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import reconciliation_policy, reconciliation_rule_service
from app.services.reconciliation_engine import Expectation, Movement, evaluate

# Real-transaction sources a recurring charge can arrive under. Excludes
# "recurring" itself: that is the generated placeholder, handled separately.
_REAL_SOURCES = ("sync", "ofx", "csv", "manual")

#: The widest window any recurring policy uses, in days. Only the SQL date
#: range is cut with it; the policy still decides, per candidate, whether a
#: charge is close enough. Kept generous on purpose: narrowing the query to
#: the exact window would silently re-implement the rule in a second place,
#: and the two would drift.
_QUERY_WINDOW_DAYS = 5


def _as_movement(transaction: Transaction) -> Movement:
    return Movement(
        amount=Decimal(transaction.amount or 0),
        currency=transaction.currency or "",
        direction=transaction.type,
        when=transaction.date,
        description=transaction.description,
        counterparty=transaction.payee,
        account_id=transaction.account_id,
        source=transaction.source,
    )


def _as_occurrence(
    recurring: RecurringTransaction, when: date
) -> Expectation:
    """One expected occurrence of a bill, as the engine sees it.

    `when` is passed rather than read off the row because the caller knows
    which occurrence is in question: the one being generated, or the next one
    due, adjusted for a weekend.
    """
    return Expectation(
        kind="recurring",
        id=recurring.id,
        amount=Decimal(recurring.amount or 0),
        currency=recurring.currency,
        direction=recurring.type,
        when=when,
        description=recurring.description,
        account_id=recurring.account_id,
    )


def _best_movement(
    candidates,
    expectation: Expectation,
    policy: dict,
) -> Optional[Transaction]:
    """The charge that best answers one expected occurrence.

    The engine takes one movement and many promises; here it is the other way
    round, so it is asked once per candidate and the answers are ranked. Best
    score wins, exactly as before: two subscriptions of the same value on one
    account are told apart by their description, not refused.
    """
    best: Optional[Transaction] = None
    # Below the range a score can take, not at its floor: a rule whose
    # similarity threshold is 0 can link a candidate scoring exactly
    # 0.0, and a strict comparison against 0.0 would drop it.
    best_score = -1.0
    for candidate in candidates:
        if candidate.is_ignored:
            continue
        decision = evaluate(_as_movement(candidate), [expectation], policy)
        if decision.port != "linked":
            continue
        if decision.score > best_score:
            best_score = decision.score
            best = candidate
    return best


async def find_real_tx_for_occurrence(
    session: AsyncSession,
    recurring: RecurringTransaction,
    occurrence_date: date,
) -> Optional[Transaction]:
    """Find an unlinked real transaction that fulfills this bill's occurrence.

    Used by generate_pending: instead of writing a duplicate placeholder for an
    occurrence a real charge already covers, link that charge to the bill.
    """
    policy = await reconciliation_rule_service.resolve_recurring(
        session, recurring.workspace_id, recurring.frequency
    )
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == recurring.account_id,
            Transaction.recurring_transaction_id.is_(None),
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.amount == recurring.amount,
            Transaction.currency == recurring.currency,
            Transaction.type == recurring.type,
            Transaction.date >= occurrence_date - timedelta(days=_QUERY_WINDOW_DAYS),
            Transaction.date <= occurrence_date + timedelta(days=_QUERY_WINDOW_DAYS),
        )
    )
    return _best_movement(
        result.scalars(), _as_occurrence(recurring, occurrence_date), policy
    )


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

    Inverted from the rest of the module: the placeholder is the thing being
    searched for, so the incoming charge plays the part of the promise and each
    placeholder is scored against it. The window is symmetric here: a
    placeholder was written for one specific occurrence, so unlike a bill it has
    no neighbour to be confused with.
    """
    policy = reconciliation_policy.default_policy("reconciliation.match_placeholder")
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source == "recurring",
            Transaction.recurring_transaction_id.is_not(None),
            Transaction.external_id.is_(None),
            Transaction.amount == amount,
            Transaction.currency == currency,
            Transaction.type == tx_type,
            Transaction.date >= tx_date - timedelta(days=_QUERY_WINDOW_DAYS),
            Transaction.date <= tx_date + timedelta(days=_QUERY_WINDOW_DAYS),
        )
    )
    incoming = Expectation(
        kind="recurring",
        id=uuid.uuid4(),
        amount=amount,
        currency=currency,
        direction=tx_type,
        when=tx_date,
        description=description,
        account_id=account_id,
    )
    return _best_movement(result.scalars(), incoming, policy)


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

    The one lookup shaped the way the engine natively is (one movement, many
    promises), except that each bill carries its own window, so each is asked
    under its own policy rather than all of them under one.
    """
    from app.services.recurring_transaction_service import adjust_weekend_date

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
    charge = Movement(
        amount=amount,
        currency=currency,
        direction=tx_type,
        when=tx_date,
        description=description,
        account_id=account_id,
    )

    best: Optional[RecurringTransaction] = None
    # Below the range a score can take, not at its floor: a rule whose
    # similarity threshold is 0 can link a candidate scoring exactly
    # 0.0, and a strict comparison against 0.0 would drop it.
    best_score = -1.0
    # The workspace's rules are fetched once for the batch and then
    # narrowed per bill, rather than queried per candidate: every bill here
    # belongs to one account, so they share a workspace, and the only thing
    # that varies between them is how often they repeat.
    composed: Optional[dict] = None
    for recurring in result.scalars():
        if composed is None:
            composed = await reconciliation_rule_service.resolve(
                session,
                recurring.workspace_id,
                reconciliation_policy.MATCH_RECURRING["node"],
            )
        occurrence = adjust_weekend_date(
            recurring.next_occurrence, recurring.weekend_adjustment
        )
        decision = evaluate(
            charge,
            [_as_occurrence(recurring, occurrence)],
            reconciliation_rule_service.narrow_for_frequency(
                composed, recurring.frequency
            ),
        )
        if decision.port == "linked" and decision.score > best_score:
            best_score = decision.score
            best = recurring
    return best


def advance_past(recurring: RecurringTransaction, fulfilled_date: date) -> None:
    """Advance a bill's next_occurrence past the occurrence a charge fulfilled.

    The target is floored at the bill's current next_occurrence: the matched
    occurrence, so an *early-posted* charge (one that lands inside the
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
