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

The amount is part of that. The queries no longer filter on it, because a
filter in SQL decides before the policy is consulted and so would silently
defeat a workspace that allows a tolerance (a USD subscription on a EUR card
is never the same figure twice). The shipped rule is still exact, so the
engine rejects a different amount just as the query used to. Where a rule
does link on an approximate amount it must say `unique_candidate`, and since
these lookups ask the engine about one candidate at a time, that guard is
enforced here instead: two fits under such a rule are a question, not a pick.
Nor does this module pick the date range: the queries read it off the policy
(`_date_reach`), so a window a workspace has widened reaches the candidates
instead of being cut off before the policy sees them.

The queries stay here, because they are what differs: three lookups, in two
directions, over two tables. Only the verdict is shared.

One dormant edge is worth naming: a bill of zero is now never matched, because
a promise with nothing outstanding is not a candidate. Nothing reaches it:
banks do not post charges of zero, but it is a difference rather than an
identity.
"""
import copy
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import reconciliation_policy, reconciliation_rule_service
from app.services.reconciliation_engine import Expectation, Movement, evaluate

# Real-transaction sources a recurring charge can arrive under. Excludes
# "recurring" itself: that is the generated placeholder, handled separately.
_REAL_SOURCES = ("sync", "import", "ofx", "csv", "manual")


def _date_reach(policy: dict[str, Any]) -> tuple[int, int]:
    """How far before and after its expectation any rule lets a movement sit.

    Only the SQL date range is cut with it; the policy still decides, per
    candidate. Read off the policy itself, so the query can never be
    narrower than a rule a workspace has widened, nor drift from it.
    """
    before = after = 0
    for strategy in policy.get("strategies", []):
        if not strategy.get("enabled", True):
            continue
        window = strategy.get("when", {}).get("date") or {}
        before = max(before, int(window.get("before_days", 0)))
        after = max(after, int(window.get("after_days", 0)))
    return before, after


def _fold(text: str) -> str:
    """Lower-cased, accents dropped, whitespace collapsed: how keywords compare."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def keyword_hit(recurring: RecurringTransaction, description: Optional[str]) -> bool:
    """Whether a bank description names this bill through its match keywords.

    A bank prints what it prints: a salary arrives as "Transferência
    Recebida", nothing like the "Salário" the bill is called, and description
    similarity can never link the two. The keywords are the person telling us
    what their bank writes, so a hit stands in for the similarity check.
    """
    if not recurring.match_keywords or not description:
        return False
    text = _fold(description)
    return any(
        _fold(keyword) in text
        for keyword in recurring.match_keywords.split(",")
        if keyword.strip()
    )


def _bill_policy(
    policy: dict[str, Any],
    recurring: RecurringTransaction,
    description: Optional[str] = None,
    late_side: str = "after_days",
) -> dict[str, Any]:
    """The policy as it applies to one bill.

    ``grace_days`` widens the side of the window on which a late charge sits,
    which is ``after_days`` when the charge is the movement and the occurrence
    the expectation, and ``before_days`` the other way round. A keyword hit on
    ``description`` drops the description check. ``amount_tolerance_percent``
    lets the amount differ by that much (a rent that carries the month's
    utilities), and because an approximate amount can fit more than one
    charge, the rule then asks for a single candidate. Returned unchanged (not
    copied) when none applies.
    """
    grace = int(recurring.grace_days or 0)
    tolerance = Decimal(recurring.amount_tolerance_percent or 0)
    hit = keyword_hit(recurring, description)
    if not grace and not hit and not tolerance:
        return policy
    policy = copy.deepcopy(policy)
    for strategy in policy.get("strategies", []):
        when = strategy.get("when", {})
        window = when.get("date")
        if grace and window is not None:
            when["date"] = {**window, late_side: int(window.get(late_side, 0)) + grace}
        if hit:
            when.pop("description_similarity", None)
        amount = when.get("amount") or {"match": "exact"}
        if tolerance and amount.get("match", "exact") in ("exact", "tolerance"):
            current = Decimal(str(amount.get("percent", "0")))
            when["amount"] = {"match": "tolerance", "percent": str(max(current, tolerance))}
            when["unique_candidate"] = True
    return policy


def _pick_across_bills(
    linked: list[tuple[Any, Any, dict[str, Any]]], composed: dict[str, Any]
) -> Any:
    """``pick_linked`` for candidates that each belong to a different bill.

    Each bill was judged under its own ``_bill_policy``, which asks for a
    single candidate when the bill matches on an approximate amount, and
    ``composed`` knows nothing of that. So when several bills linked under the
    winning rule and any of them did so under such a policy, none wins: the
    charge goes to a person, as it would for one bill with two close charges.
    """
    winner, rivals = reconciliation_rule_service.pick_linked(
        [(candidate, decision) for candidate, decision, _ in linked], composed
    )
    if winner is None or len(rivals) < 2:
        return winner
    policies = {id(candidate): policy for candidate, _, policy in linked}
    for candidate, decision in rivals:
        strategy = next(
            (s for s in policies[id(candidate)].get("strategies", []) if s["id"] == decision.strategy),
            {},
        )
        if strategy.get("when", {}).get("unique_candidate", False):
            return None
    return winner


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
    policy_for: Optional[Callable[[Transaction], dict]] = None,
) -> Optional[Transaction]:
    """The charge that best answers one expected occurrence.

    The engine takes one movement and many promises; here it is the other way
    round, so it is asked once per candidate and the answers are ranked: the
    earlier rule first, then the best score. Two subscriptions of the same
    value on one account are told apart by their description, not refused,
    unless the rule that linked them asks for a single candidate. On an equal
    score the first candidate passed in wins, and the callers pass them nearest
    first.
    """
    linked = []
    for candidate in candidates:
        if candidate.is_ignored:
            continue
        decision = evaluate(
            _as_movement(candidate),
            [expectation],
            policy_for(candidate) if policy_for else policy,
        )
        if decision.port == "linked":
            linked.append((candidate, decision))
    return reconciliation_rule_service.pick_linked(linked, policy)[0]


async def find_real_tx_for_occurrence(
    session: AsyncSession,
    recurring: RecurringTransaction,
    occurrence_date: date,
) -> Optional[Transaction]:
    """Find an unlinked real transaction that fulfills this bill's occurrence.

    Used by generate_pending: instead of writing a duplicate placeholder for an
    occurrence a real charge already covers, link that charge to the bill.
    """
    policy = _bill_policy(
        await reconciliation_rule_service.resolve_recurring(
            session, recurring.workspace_id, recurring.frequency
        ),
        recurring,
    )
    # The charge is the movement and the occurrence the expectation, so the
    # charge may be up to `before` days earlier than the occurrence, or `after`
    # days later.
    before, after = _date_reach(policy)
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == recurring.account_id,
            Transaction.recurring_transaction_id.is_(None),
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.currency == recurring.currency,
            Transaction.type == recurring.type,
            Transaction.date >= occurrence_date - timedelta(days=before),
            Transaction.date <= occurrence_date + timedelta(days=after),
        )
    )
    # The score only reads the description, so two charges can tie, and with a
    # window as long as the billing period that is no rare thing. Looking at
    # the nearest first makes the nearest win the tie rather than the database;
    # at equal distance the earlier wins, a fixed order rather than the
    # database's.
    nearest = sorted(
        result.scalars(),
        key=lambda tx: (abs((tx.date - occurrence_date).days), tx.date, tx.id),
    )
    return _best_movement(
        nearest,
        _as_occurrence(recurring, occurrence_date),
        policy,
        policy_for=lambda tx: _bill_policy(policy, recurring, tx.description)
        if keyword_hit(recurring, tx.description)
        else policy,
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
    placeholder is scored against it. The shipped window is symmetric here: a
    placeholder was written for one specific occurrence, so unlike a bill it has
    no neighbour to be confused with.

    The shipped placeholder rule judges first and is exact. When it links
    nothing, the placeholders are judged again the way the workspace's
    recurring rule is written: the charge is the movement and each placeholder
    the occurrence it answers, because the placeholder is an occurrence of its
    bill. A workspace that lets the bill match a charge of a slightly
    different amount means the placeholder too, or the charge would be written
    next to it instead, and the rule's window and tolerance only mean what
    they say read that way round.

    The two passes read the window in opposite directions (the placeholder is
    the movement in one, the expectation in the other), so the query reaches as
    far as the wider of the two on each side and the policies decide from
    there. The workspace's rule is resolved before the query for that reason.
    """
    account = await session.get(Account, account_id)
    if account is None:
        return None
    policy = reconciliation_policy.default_policy("reconciliation.match_placeholder")
    composed = await reconciliation_rule_service.resolve(
        session,
        account.workspace_id,
        reconciliation_policy.MATCH_RECURRING["node"],
    )
    # Narrowing for frequency only ever shrinks a window, so the unnarrowed
    # rule is an upper bound for the query.
    p_before, p_after = _date_reach(policy)
    r_before, r_after = _date_reach(composed)
    max_grace = 15  # the most grace_days a bill may carry
    result = await session.execute(
        select(Transaction, RecurringTransaction)
        .join(
            RecurringTransaction,
            RecurringTransaction.id == Transaction.recurring_transaction_id,
        )
        .where(
            Transaction.account_id == account_id,
            Transaction.source == "recurring",
            Transaction.recurring_transaction_id.is_not(None),
            Transaction.external_id.is_(None),
            Transaction.currency == currency,
            Transaction.type == tx_type,
            Transaction.date >= tx_date - timedelta(days=max(p_before, r_after) + max_grace),
            Transaction.date <= tx_date + timedelta(days=max(p_after, r_before)),
        )
    )
    # The score only reads the description, so tied placeholders go to the
    # nearest in date, which both passes honour by seeing it first; at equal
    # distance the earlier wins, a fixed order rather than the database's.
    rows = sorted(
        result.all(),
        key=lambda row: (abs((row[0].date - tx_date).days), row[0].date, row[0].id),
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
    bills = {row[0].id: row[1] for row in rows}
    # Here the placeholder is the movement and the charge the expectation, so
    # a charge that arrives late leaves the placeholder *before* it.
    found = _best_movement(
        [row[0] for row in rows],
        incoming,
        policy,
        policy_for=lambda placeholder: _bill_policy(
            policy, bills[placeholder.id], description, late_side="before_days"
        ),
    )
    if found is not None or not rows:
        return found

    charge = Movement(
        amount=amount,
        currency=currency,
        direction=tx_type,
        when=tx_date,
        description=description,
        account_id=account_id,
    )
    linked = []
    for placeholder, bill in rows:
        if placeholder.is_ignored:
            continue
        policy = _bill_policy(
            reconciliation_rule_service.narrow_for_frequency(composed, bill.frequency),
            bill,
            description,
        )
        decision = evaluate(
            charge,
            [
                Expectation(
                    kind="recurring",
                    id=placeholder.id,
                    amount=Decimal(placeholder.amount or 0),
                    currency=placeholder.currency,
                    direction=placeholder.type,
                    when=placeholder.date,
                    description=placeholder.description,
                    account_id=placeholder.account_id,
                )
            ],
            policy,
        )
        if decision.port == "linked":
            linked.append((placeholder, decision, policy))
    return _pick_across_bills(linked, composed)


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
    """Find an active bill whose expected occurrence this charge fulfills.

    Used when a real charge arrives before any placeholder was generated. The
    caller stamps the charge with the bill and advances the bill past the
    fulfilled occurrence so generate_pending won't later duplicate it.

    The one lookup shaped the way the engine natively is (one movement, many
    promises), except that each bill carries its own window, so each is asked
    under its own policy rather than all of them under one.
    """
    from app.services.recurring_transaction_service import nearest_occurrence

    result = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.account_id == account_id,
            RecurringTransaction.is_active.is_(True),
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

    linked = []
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
        # The occurrence nearest the charge, not only the one the pointer is
        # on: a pointer that fell behind (a month nothing matched) would
        # otherwise never meet a charge again, and every later month would
        # be projected next to the charge that paid it.
        _, occurrence = nearest_occurrence(recurring, tx_date)
        policy = _bill_policy(
            reconciliation_rule_service.narrow_for_frequency(
                composed, recurring.frequency
            ),
            recurring,
            description,
        )
        decision = evaluate(charge, [_as_occurrence(recurring, occurrence)], policy)
        if decision.port == "linked":
            linked.append((recurring, decision, policy))
    if composed is None:
        return None
    return _pick_across_bills(linked, composed)


def advance_past(recurring: RecurringTransaction, fulfilled_date: date) -> None:
    """Advance a bill's next_occurrence past the occurrence a charge fulfilled.

    The fulfilled occurrence is the one nearest the charge, the same one
    find_bill_for_incoming judged it against, so an *early-posted* charge (a
    Jan 8 charge for a Jan 10 occurrence) still moves the pointer past Jan 10,
    and a charge for a later occurrence than the pointer's (the pointer fell
    behind) moves it past that one rather than one period at a time.

    Deactivates the bill if it advances beyond its end_date. Lazy-imports the
    date helper to avoid a circular import with recurring_transaction_service.
    """
    from app.services.recurring_transaction_service import (
        _advance_date,
        nearest_occurrence,
    )

    intended_day = recurring.day_of_month or recurring.start_date.day
    target, _ = nearest_occurrence(recurring, fulfilled_date)
    guard = 0
    while recurring.next_occurrence <= target and guard < 500:
        recurring.next_occurrence = _advance_date(
            recurring.next_occurrence, recurring.frequency, intended_day=intended_day
        )
        guard += 1
    if recurring.end_date and recurring.next_occurrence > recurring.end_date:
        recurring.is_active = False
