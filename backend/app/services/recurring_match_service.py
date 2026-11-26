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
import uuid
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

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


def _at_the_charge(
    promise: Expectation, amount: Decimal, currency: str
) -> Expectation:
    """The same promise, read in the money the charge actually moved.

    A bill kept in a foreign currency (a USD subscription on a VND or BRL
    card) is charged in the account's currency at that month's rate, so both
    its amount and its currency move every month and the engine, which never
    converts, refuses it on both counts (#1073). The promise is therefore
    offered under the charge's own amount and currency. Every other
    condition the workspace's rule carries still decides: whether the rule
    is enabled at all, the window, the description similarity, the account
    and the direction.
    """
    return replace(promise, amount=amount, currency=currency)


def _only_fit(fits: list) -> Optional[Any]:
    """One candidate or none: a rate-blind match is not worth guessing at."""
    return fits[0] if len(fits) == 1 else None


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
    alternative_descriptions: Optional[dict[uuid.UUID, str]] = None,
) -> Optional[Transaction]:
    """The charge that best answers one expected occurrence.

    The engine takes one movement and many promises; here it is the other way
    round, so it is asked once per candidate and the answers are ranked: the
    earlier rule first, then the best score. Two subscriptions of the same
    value on one account are told apart by their description, not refused,
    unless the rule that linked them asks for a single candidate. On an equal
    score the first candidate passed in wins, and the callers pass them nearest
    first.

    `alternative_descriptions` gives a candidate a second wording to be scored
    by when the first one does not link. A generated placeholder answers to the
    wording it was written with even after a `set_description` rule rewrote it:
    the charge at the bank still carries that text, so both must be offered or
    a renamed placeholder could never be found by it.
    """
    linked = []
    for candidate in candidates:
        if candidate.is_ignored:
            continue
        decision = _evaluate_candidate(
            _as_movement(candidate), expectation, policy,
            (alternative_descriptions or {}).get(candidate.id),
        )
        if decision.port == "linked":
            linked.append((candidate, decision))
    return reconciliation_rule_service.pick_linked(linked, policy)[0]


def _evaluate_candidate(
    movement: Movement,
    expectation: Expectation,
    policy: dict,
    alternative_description: Optional[str] = None,
) -> Any:
    """Judge one candidate, retrying it under a second wording if it did not link.

    Only a wording that differs from the first is worth a second pass, and only
    when the first pass linked nothing: a candidate the rule already refused on
    the amount or the date is not rescued by the description.
    """
    decision = evaluate(movement, [expectation], policy)
    if (
        decision.port != "linked"
        and alternative_description
        and alternative_description != movement.description
    ):
        decision = evaluate(
            replace(movement, description=alternative_description),
            [expectation],
            policy,
        )
    return decision


async def occurrence_reach(
    session: AsyncSession, workspace_id: uuid.UUID, frequency: str,
) -> tuple[int, int]:
    """Days a charge may sit before and after an occurrence it fulfills."""
    return _date_reach(
        await reconciliation_rule_service.resolve_recurring(session, workspace_id, frequency)
    )


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
    # The charge is the movement and the occurrence the expectation, so the
    # charge may be up to `before` days earlier than the occurrence, or `after`
    # days later.
    before, after = _date_reach(policy)
    result = await session.execute(
        select(Transaction).where(
            # Sync can move the account to another workspace; its charges
            # then belong there and never link to this bill.
            Transaction.workspace_id == recurring.workspace_id,
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
    occurrence = _as_occurrence(recurring, occurrence_date)
    found = _best_movement(nearest, occurrence, policy)
    if found is not None:
        return found

    # A bill in a currency other than its account's is charged in the
    # account's currency, which the policy never links (#1073).
    account = await session.get(Account, recurring.account_id)
    if account is None or not account.currency or account.currency == recurring.currency:
        return None
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == recurring.workspace_id,
            Transaction.account_id == recurring.account_id,
            Transaction.recurring_transaction_id.is_(None),
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.currency == account.currency,
            Transaction.type == recurring.type,
            Transaction.date >= occurrence_date - timedelta(days=before),
            Transaction.date <= occurrence_date + timedelta(days=after),
        )
    )
    fits = []
    for charge in result.scalars():
        if charge.is_ignored:
            continue
        decision = evaluate(
            _as_movement(charge),
            [
                _at_the_charge(
                    occurrence,
                    Decimal(charge.amount or 0),
                    charge.currency or "",
                )
            ],
            policy,
        )
        if decision.port == "linked":
            fits.append(charge)
    return _only_fit(fits)


def _placeholder_reach(policy: dict[str, Any], composed: dict[str, Any]) -> tuple[int, int]:
    """Days before and after a charge the placeholder query has to reach.

    The two passes of `find_placeholder_for_incoming` read the window in
    opposite directions, so it is the wider of the two on each side.
    Narrowing for frequency only ever shrinks a window, so the unnarrowed
    rule is an upper bound.
    """
    p_before, p_after = _date_reach(policy)
    r_before, r_after = _date_reach(composed)
    return max(p_before, r_after), max(p_after, r_before)


def _placeholders_query(account_id: uuid.UUID, date_lo: date, date_hi: date):
    """Generated rows of an account still waiting for their charge, with the
    frequency of their bill."""
    return (
        select(Transaction, RecurringTransaction.frequency)
        .join(
            RecurringTransaction,
            RecurringTransaction.id == Transaction.recurring_transaction_id,
        )
        .where(
            Transaction.account_id == account_id,
            Transaction.source == "recurring",
            Transaction.recurring_transaction_id.is_not(None),
            Transaction.external_id.is_(None),
            Transaction.date >= date_lo,
            Transaction.date <= date_hi,
        )
    )


@dataclass
class IncomingMatchPool:
    """What the recurring lookups of one batch read, loaded once.

    An import asks both lookups about every row of one account. The
    workspace's recurring rule, the account's bills and its placeholders
    near the batch's dates are read here once, and each row is then judged
    in memory on the same terms the per-row queries use. The objects are the
    session's own, so a placeholder an earlier row upgraded (it is no longer
    `source="recurring"`) or a bill it advanced is seen as it now is.
    """

    composed: dict[str, Any]
    placeholders: list[tuple[Transaction, str]]
    bills: list[RecurringTransaction]

    def placeholders_between(self, date_lo, date_hi, tx_type, currency_fits):
        return [
            (placeholder, frequency)
            for placeholder, frequency in self.placeholders
            if placeholder.source == "recurring"
            and placeholder.recurring_transaction_id is not None
            and placeholder.external_id is None
            and placeholder.type == tx_type
            and currency_fits(placeholder.currency)
            and date_lo <= placeholder.date <= date_hi
        ]


async def load_incoming_pool(
    session: AsyncSession,
    user_id: uuid.UUID,
    account: Account,
    first_date: date,
    last_date: date,
) -> IncomingMatchPool:
    """Read once what the lookups below need for charges dated in
    [first_date, last_date] on `account`."""
    composed = await reconciliation_rule_service.resolve(
        session, account.workspace_id, reconciliation_policy.MATCH_RECURRING["node"]
    )
    reach_before, reach_after = _placeholder_reach(
        reconciliation_policy.default_policy("reconciliation.match_placeholder"),
        composed,
    )
    placeholders = await session.execute(
        _placeholders_query(
            account.id,
            first_date - timedelta(days=reach_before),
            last_date + timedelta(days=reach_after),
        )
    )
    bills = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.account_id == account.id,
            RecurringTransaction.is_active.is_(True),
        )
    )
    return IncomingMatchPool(
        composed=composed,
        placeholders=[(row[0], row[1]) for row in placeholders.all()],
        bills=list(bills.scalars().all()),
    )


async def find_placeholder_for_incoming(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    tx_type: str,
    tx_date: date,
    description: Optional[str],
    *,
    pool: Optional[IncomingMatchPool] = None,
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

    A placeholder's current description is not the only wording it answers to.
    A rule may have rewritten it since it was generated (`set_description`),
    and the charge at the bank still carries the wording the placeholder was
    written with, which `set_description` keeps in `original_description`.
    Both are offered to the engine in each pass, the current text first, so a
    renamed placeholder is found by the charge it was written for. The bill's
    own description is deliberately not read here: it is today's wording, and
    an occurrence carries the one it was generated with.
    """
    account = await session.get(Account, account_id)
    if account is None:
        return None
    policy = reconciliation_policy.default_policy("reconciliation.match_placeholder")
    composed = (
        pool.composed
        if pool is not None
        else await reconciliation_rule_service.resolve(
            session,
            account.workspace_id,
            reconciliation_policy.MATCH_RECURRING["node"],
        )
    )
    reach_before, reach_after = _placeholder_reach(policy, composed)
    date_lo = tx_date - timedelta(days=reach_before)
    date_hi = tx_date + timedelta(days=reach_after)
    if pool is not None:
        found_rows = pool.placeholders_between(
            date_lo, date_hi, tx_type, lambda c: c == currency
        )
    else:
        result = await session.execute(
            _placeholders_query(account_id, date_lo, date_hi).where(
                Transaction.currency == currency, Transaction.type == tx_type
            )
        )
        found_rows = result.all()
    # The score only reads the description, so tied placeholders go to the
    # nearest in date, which both passes honour by seeing it first; at equal
    # distance the earlier wins, a fixed order rather than the database's.
    rows = sorted(
        found_rows,
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
    charge = Movement(
        amount=amount,
        currency=currency,
        direction=tx_type,
        when=tx_date,
        description=description,
        account_id=account_id,
    )
    found = _best_movement(
        [row[0] for row in rows],
        incoming,
        policy,
        {row[0].id: row[0].original_description for row in rows},
    )
    if found is None and rows:
        found = _placeholder_under_workspace_rule(rows, charge, composed)
    if found is not None:
        return found

    # A placeholder for a bill kept in another currency, answered by a
    # charge in the account's own currency (#1073). The caller takes the
    # charge's amount and currency onto the row. The placeholder is offered
    # to the workspace's rule in the charge's currency, so a disabled rule
    # still links nothing.
    if not account.currency or currency != account.currency:
        return None
    if pool is not None:
        foreign_rows = pool.placeholders_between(
            date_lo, date_hi, tx_type, lambda c: c is not None and c != currency
        )
    else:
        result = await session.execute(
            _placeholders_query(account_id, date_lo, date_hi).where(
                Transaction.currency != currency, Transaction.type == tx_type
            )
        )
        foreign_rows = result.all()
    fits = []
    for placeholder, frequency in foreign_rows:
        if placeholder.is_ignored:
            continue
        promise = _at_the_charge(_as_placeholder(placeholder), amount, currency)
        narrowed = reconciliation_rule_service.narrow_for_frequency(composed, frequency)
        decision = evaluate(charge, [promise], narrowed)
        if (
            decision.port != "linked"
            and placeholder.original_description
            and placeholder.original_description != promise.description
        ):
            decision = evaluate(
                charge,
                [replace(promise, description=placeholder.original_description)],
                narrowed,
            )
        if decision.port == "linked":
            fits.append(placeholder)
    return _only_fit(fits)


def _as_placeholder(placeholder: Transaction) -> Expectation:
    """A generated row as the promise it stands for."""
    return Expectation(
        kind="recurring",
        id=placeholder.id,
        amount=Decimal(placeholder.amount or 0),
        currency=placeholder.currency,
        direction=placeholder.type,
        when=placeholder.date,
        description=placeholder.description,
        account_id=placeholder.account_id,
    )


def _placeholder_under_workspace_rule(
    rows, charge: Movement, composed: dict
) -> Optional[Transaction]:
    """The second pass of `find_placeholder_for_incoming`: the charge is the
    movement and each placeholder the occurrence it answers."""
    linked = []
    for placeholder, frequency in rows:
        if placeholder.is_ignored:
            continue
        occurrence = _as_placeholder(placeholder)
        narrowed = reconciliation_rule_service.narrow_for_frequency(composed, frequency)
        decision = evaluate(charge, [occurrence], narrowed)
        if (
            decision.port != "linked"
            and placeholder.original_description
            and placeholder.original_description != placeholder.description
        ):
            decision = evaluate(
                charge,
                [replace(occurrence, description=placeholder.original_description)],
                narrowed,
            )
        if decision.port == "linked":
            linked.append((placeholder, decision))
    return reconciliation_rule_service.pick_linked(linked, composed)[0]


async def find_bill_for_incoming(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    tx_type: str,
    tx_date: date,
    description: Optional[str],
    *,
    pool: Optional[IncomingMatchPool] = None,
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

    if pool is not None:
        bills = [
            bill
            for bill in pool.bills
            # An earlier row may have advanced a bill past its end date,
            # which deactivates it.
            if bill.is_active
            and bill.account_id == account_id
            and bill.currency == currency
            and bill.type == tx_type
        ]
    else:
        result = await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.user_id == user_id,
                RecurringTransaction.account_id == account_id,
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.currency == currency,
                RecurringTransaction.type == tx_type,
            )
        )
        bills = list(result.scalars())
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
    for recurring in bills:
        if composed is None and pool is not None:
            composed = pool.composed
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
        if decision.port == "linked":
            linked.append((recurring, decision))
    if composed is None:
        return None
    return reconciliation_rule_service.pick_linked(linked, composed)[0]


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
