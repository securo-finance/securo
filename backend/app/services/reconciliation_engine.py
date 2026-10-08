"""Deciding which expectation a movement of money settles.

**This module decides and never writes.** It touches no session, loads
nothing, and returns a decision plus the reasoning that produced it.
Applying that decision (creating an allocation, or upgrading a
placeholder in place) belongs to the caller, and is deliberately a
separate step: a function that mutates cannot be dry-run, and "show me
what would happen before it happens" is the whole point of putting this
in front of a person.

## What an expectation is

An invoice and a scheduled recurring occurrence are the same thing seen
from here: **a promise that money will move, waiting for the movement
that confirms it.** A workspace that never issues an invoice still has
promises (the rent leaving on the 5th, the retainer arriving on the
20th), and they want matching for exactly the same reason.

So both arrive here as an `Expectation` and are scored by the same
signals. What they do *not* share is what happens afterwards, and that
difference is real rather than cosmetic:

  - An invoice link is **N:N with an amount**, and both rows survive: one
    payout settles a dozen invoices net of fees.
  - A recurring occurrence is **1:1**, and the placeholder *is* the
    transaction: the real charge replaces it in place, so there is only
    ever one row.

Forcing those into one "apply" would produce a function with a mode
switch and two half-truths. They stay apart; only the decision is shared.

## Ports, not a boolean

`linked` · `suggested` · `unmatched`. The direction for automations is a
node graph rather than a bigger predicate builder, so this is shaped as a
node from the first line: named outputs, and a trace saying which
strategy fired and why the others did not.
"""
from __future__ import annotations

import uuid
import itertools
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any, Literal, Optional

from app.services.text_similarity import names_account, token_overlap

#: What the decision came out of. Named rather than boolean so a node
#: graph can wire each one somewhere different later.
Port = Literal["linked", "suggested", "unmatched"]

#: Which kind of promise a candidate is. The engine treats them alike;
#: the caller uses this to pick how to apply the decision.
#:
#: `transaction` is the odd one and worth naming: the other two are
#: promises somebody wrote down, while this one is the other leg of a
#: transfer, which is a promise only by implication. Money leaving one
#: account **is** the expectation that the same money arrives in another,
#: so the leg is carried here with its direction flipped: a debit of 100
#: is an expectation of a credit of 100. That flip is what lets the
#: direction check below go on meaning what it says instead of needing a
#: mode for transfers.
ExpectationKind = Literal["invoice", "recurring", "transaction"]

#: **Which way round the match is being made.** Matching happens at two
#: different moments and they are not the same question.
#:
#:   - `money_arrives`: a payment lands and we look for the promise it
#:     answers. The promise came first and was waiting.
#:   - `invoice_issued`: a document is written and we look back at money
#:     that arrived before it existed: the client who pays and lets the
#:     nota follow.
#:
#: The evidence is weaker in the second: money that was already sitting
#: there had a life of its own, and might have been a refund or another
#: job. So a rule says which moments it is willing to run in, and that
#: choice is on the page rather than buried in whichever function happens
#: to be doing the looking.
Trigger = Literal["money_arrives", "invoice_issued", "both"]


class Reason(str, Enum):
    """Why a strategy did not fire, in words a UI can show.

    A rejected candidate is more useful than a silent one: "no strategy
    matched" tells a person nothing, while "the amount was right and the
    date was eleven days outside the window" tells them what to change.
    """

    DIRECTION = "direction_differs"
    CURRENCY = "currency_differs"
    COUNTERPARTY = "different_counterparty"
    #: Both legs are on the same account, so nothing moved between
    #: accounts. Its own reason rather than `COUNTERPARTY`, because a
    #: person reading a transfer's trace is asking about accounts and
    #: being told "different counterparty" would send them looking at
    #: payees.
    SAME_ACCOUNT = "same_account"
    #: Neither side's text names the other side's account, and this rule
    #: only trusts a pair that says where it is going.
    ACCOUNT_NOT_NAMED = "neither_description_names_the_other_account"
    #: The rule was written for legs on a particular kind of account (a
    #: credit card, typically) and neither of these is one.
    ACCOUNT_TYPE = "account_type_not_covered"
    #: It fitted, and another candidate fitted the same way while sitting
    #: closer in time. Not a failure of this pair so much as a statement
    #: that a better one was available, and worth saying in those words:
    #: *rejected* would send somebody looking for what was wrong with it.
    FURTHER_IN_TIME = "another_candidate_is_closer_in_time"
    AMOUNT = "amount_outside_tolerance"
    DATE = "date_outside_window"
    DESCRIPTION = "description_too_different"
    ALREADY_SETTLED = "nothing_left_to_settle"
    AMBIGUOUS = "several_candidates_matched"
    SOURCE_IGNORED = "transaction_source_ignored"
    #: The rule does not apply to this money at all: wrong account, wrong
    #: currency, outside the amount band, text that does not match. Kept
    #: apart from the comparison reasons above because it answers a
    #: different question: not "why did this pair fail" but "why was this
    #: rule not even consulted".
    OUT_OF_SCOPE = "rule_does_not_apply_here"
    #: The rule runs at the other moment: when money arrives rather than
    #: when a document is written, or the reverse.
    WRONG_MOMENT = "rule_runs_at_another_moment"
    #: More than one combination of promises adds up to this payment, and
    #: they are equally good. A person picks.
    AMBIGUOUS_SET = "several_combinations_add_up"
    #: Too many promises are open to work out which combination this
    #: payment covers without guessing.
    TOO_MANY_TO_COMBINE = "too_many_candidates_to_combine"


@dataclass(frozen=True)
class Movement:
    """The money that actually moved, reduced to what matching needs.

    Built from a `Transaction` by the caller. A value object rather than
    the ORM row so this module stays pure and trivially testable, and so
    a future intake that is not a `Transaction` (a gateway payout line,
    say) can be scored without pretending to be one.
    """

    amount: Decimal
    currency: str
    #: `credit` (money in) or `debit` (money out).
    direction: str
    when: date
    description: Optional[str] = None
    #: The counterparty exactly as the bank printed it, before anything of
    #: ours resolved it to a person. Kept apart from `payee_id` because a
    #: rule may need it long before the name has been mapped to anybody:
    #: a Pix description is generic and the payer's name arrives in its
    #: own field, so a rule that could only read `description` could not
    #: express "the transfers that come from this company", which is the
    #: identifying fact on the most common inflow there is.
    counterparty: Optional[str] = None
    payee_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    #: What the account is called, and what kind it is. Only transfer
    #: matching reads them, and it needs both: the name because the one
    #: signal that reliably tells two same-amount transfers apart is a
    #: description naming where the money went, and the kind because a
    #: leg on a credit card deserves a different answer from a leg
    #: between two checking accounts.
    account_name: Optional[str] = None
    account_type: Optional[str] = None
    #: `sync`, `ofx`, `csv`, `manual`, `recurring`. A policy may ignore
    #: some: the receivable node ignores `recurring`, because a
    #: generated placeholder is a promise and not the money itself.
    source: Optional[str] = None


@dataclass(frozen=True)
class Expectation:
    """A promise of money moving, waiting to be confirmed.

    `amount` is what is **still outstanding**, never the original total:
    an invoice half paid expects the other half, and matching it against
    its full value would miss every second instalment.
    """

    kind: ExpectationKind
    id: uuid.UUID
    amount: Decimal
    currency: str
    direction: str
    when: date
    description: Optional[str] = None
    #: As printed by the bank, when this expectation is itself a
    #: statement line. The identifying words land in whichever of the two
    #: fields the bank felt like using, so a signal that read only
    #: `description` would miss the half of them that use the other.
    counterparty: Optional[str] = None
    payee_id: Optional[uuid.UUID] = None
    #: Set for a recurring bill, which is charged to a known account.
    #: Null for an invoice, where the money may land anywhere.
    account_id: Optional[uuid.UUID] = None
    #: As on `Movement`, and read by the same rules. An invoice and a
    #: recurring bill leave them null; the other leg of a transfer is an
    #: account and fills both.
    account_name: Optional[str] = None
    account_type: Optional[str] = None
    #: When the promise came into existence, if that is a different day
    #: from when it comes due. An invoice has both and they are weeks
    #: apart; a recurring occurrence has one, and leaves this null.
    issued: Optional[date] = None
    #: The amounts at which this promise closes a whole part, smallest
    #: first, ending at `amount`. Set when the money was agreed in
    #: installments: a client paying the next one, or the next two at
    #: once, is paying exactly what was asked, and comparing that against
    #: the whole balance would call every installment a mismatch. Empty
    #: for a promise that is one amount on one date.
    stops: tuple[Decimal, ...] = ()

    def targets(self) -> tuple[Decimal, ...]:
        """What a movement's amount is compared against, in order."""
        return self.stops or (self.amount,)


@dataclass(frozen=True)
class Settlement:
    """One promise, and how much of this movement goes against it.

    Exists because a payment does not always answer exactly one thing. A
    client clearing three of their own invoices in a single transfer is
    ordinary, and so is a commercial arrangement that settles a month's
    worth at once. The ledger has always been able to record that
    (allocations are many-to-many with an amount on each), but a decision
    that could only ever name one promise could never propose it.
    """

    expectation: Expectation
    amount: Decimal
    #: The part of the promise the amount was measured against: the next
    #: installment, say, rather than the whole balance. None when that is
    #: the balance itself.
    target: Optional[Decimal] = None


@dataclass
class Consideration:
    """One candidate, and what the engine made of it."""

    expectation_id: uuid.UUID
    strategy: Optional[str] = None
    rejected_by: Optional[Reason] = None
    score: float = 0.0


@dataclass
class Decision:
    """What to do, and the reasoning that got there.

    `trace` carries every candidate that was looked at, including the
    ones that lost. It is what lets the invoice screen answer "why is
    this linked" with the name of the rule that fired, and "why is this
    not" with the specific signal that failed.
    """

    port: Port
    #: Everything this movement settles. Usually one entry; several when a
    #: single payment answers several promises at once.
    settlements: list[Settlement] = field(default_factory=list)
    #: The first settlement, spelled out, because one promise is the
    #: overwhelming majority and every caller written before sets existed
    #: still reads these. Never a *different* answer from `settlements`:
    #: its head.
    expectation: Optional[Expectation] = None
    strategy: Optional[str] = None
    amount: Optional[Decimal] = None
    #: Set when a strategy matched a known fraction rather than the whole
    #: Brazilian withholding is the reason this exists. The caller
    #: books the difference; the engine only names it.
    difference: Optional[Decimal] = None
    difference_kind: Optional[str] = None
    #: How well the winner scored, 1.0 when the strategy carries no
    #: graded signal. A caller holding several movements for one
    #: expectation ranks them by this: the recurring matcher does, and
    #: has always taken the better-matching charge rather than refusing
    #: both. Exposed rather than dug out of the trace because it is also
    #: what a suggestion has to show a person to be worth showing. Zero
    #: when nothing was decided, so an unmatched result never sorts above
    #: a real one.
    score: float = 0.0
    trace: list[Consideration] = field(default_factory=list)


def _amount_verdict(
    movement: Decimal, expected: Decimal, rule: dict[str, Any], ratios: list[Decimal]
) -> tuple[bool, Optional[Decimal], Optional[str]]:
    """Does the money that moved answer the amount expected?

    Returns whether it matched, the unexplained difference, and what that
    difference is. `ratio` is the one that carries meaning: money arriving
    at a known fraction of the invoice is withholding, not an error, and
    the caller books it rather than asking a person about it.
    """
    match = rule.get("match", "exact")
    moved = abs(movement)
    want = abs(expected)

    if match == "exact":
        return moved == want, None, None

    if match == "tolerance":
        percent = Decimal(str(rule.get("percent", "0")))
        allowed = (want * percent / Decimal("100")).copy_abs()
        return abs(moved - want) <= allowed, None, None

    if match == "partial":
        # Money that covers *part* of what is owed. Two transactions
        # settling one invoice is ordinary (an instalment, a client
        # paying what they had), and the ledger has always supported it,
        # but without this mode nothing could ever propose the first half:
        # every other mode compares against the whole outstanding balance
        # and a half is simply not it.
        #
        # Strictly less than, so this and `exact` never both fire and a
        # trace never has to explain which one won.
        if moved >= want or moved <= Decimal("0"):
            return False, None, None

        floor = Decimal(str(rule.get("min_ratio", "0")))
        if floor > 0 and moved < (want * floor):
            # A token amount against a large invoice is noise, not an
            # instalment. Offering it would teach people to ignore the
            # queue.
            return False, None, None

        ceiling = Decimal(str(rule.get("max_ratio", "1")))
        if ceiling < 1 and moved > (want * ceiling):
            # Money that is *almost* the whole thing is not an instalment
            # either: it is a fee, a withholding, a rounding. Calling it
            # a part payment would put a confident wrong word on the
            # queue, and the tolerance rules are the ones written for it.
            return False, None, None

        return True, (want - moved), "part_payment"

    if match == "ratio":
        epsilon = Decimal(str(rule.get("epsilon", "0")))
        for ratio in ratios:
            target = (want * ratio).quantize(Decimal("0.01"))
            if abs(moved - target) <= epsilon:
                return True, (want - moved), rule.get("difference_kind")
        return False, None, None

    return False, None, None


def _within_window(
    movement_date: date,
    expected: date,
    rule: dict[str, Any],
    issued: Optional[date] = None,
) -> bool:
    """Is this money close enough in time to be this promise's?

    Asymmetric on purpose: money arrives *after* a due date far more
    often than before it, and a symmetric window either misses the late
    ones or reaches into the neighbouring occurrence.

    The two sides are also measured from different days when the
    expectation has both. **Late is late by reference to the due date;
    early is early by reference to the day the promise was made.** An
    invoice due on the 30th and issued on the 1st is not "29 days paid
    early" when the client pays on the 2nd: it is paid the day after it
    was issued, which is the best case there is. Collapsing both onto the
    due date is what would push every deposit and every pay-then-invoice
    into the rejected pile. A recurring occurrence has no separate issue
    date, leaves `issued` null, and keeps the single-anchor behaviour it
    has always had.
    """
    before = int(rule.get("before_days", 0))
    after = int(rule.get("after_days", 0))
    return (
        ((issued or expected) - movement_date).days <= before
        and (movement_date - expected).days <= after
    )


#: How many open promises a set rule will consider at once, and how many
#: it will put in one answer. Both are hard limits rather than
#: preferences: finding which invoices add up to a payment is subset-sum,
#: which grows explosively, and a sync that hangs is worse than a match
#: that is not made. Beyond these the engine says so and stops.
MAX_SET_CANDIDATES = 12
MAX_SET_SIZE = 6


def _combinations_that_add_up(
    moved: Decimal,
    candidates: list[Expectation],
    percent: Decimal,
    max_size: int,
) -> list[list[Expectation]]:
    """Which groups of promises this payment could be covering.

    Every group that fits, not the first one found, because the number of
    answers *is* the finding. One combination is a match; two are a
    question, and picking the first would be inventing certainty exactly
    where the single-promise path refuses to.

    Search order is smallest group first, so a payment that settles one
    invoice exactly is never reported as also settling three that happen
    to sum to the same figure.
    """
    found: list[list[Expectation]] = []
    usable = [c for c in candidates if c.amount > Decimal("0")]
    if len(usable) > MAX_SET_CANDIDATES:
        return []

    for size in range(2, min(max_size, len(usable)) + 1):
        for group in itertools.combinations(usable, size):
            total = sum((c.amount for c in group), Decimal("0"))
            # The allowance is a share of what the invoices are worth, not
            # of what arrived. A gateway's cut is a percentage of the
            # gross, and "two per cent" should mean the thing a person
            # means by it rather than two per cent of the figure left
            # after the cut.
            tolerance = (total * percent / Decimal("100")).copy_abs()
            if abs(total - moved) <= tolerance:
                found.append(list(group))
                # Two is already an answer of "ask a person", so there is
                # nothing to learn from finding a third.
                if len(found) > 1:
                    return found
    return found


def _in_scope(
    movement: Movement, rule: dict[str, Any], base_currency: Optional[str]
) -> bool:
    """Does this rule apply to this money at all?

    Separate from the comparisons below, and worth naming as its own idea.
    Every other signal asks *how well the pair fits*; these ask *whether
    the rule was written for money like this*: a specific account, a
    currency that is not the one you normally deal in, an amount above the
    threshold where you stop trusting an automatic match, a statement line
    whose text you recognise.

    That distinction is what the rule tools converged on independently:
    they let a rule name its bank account, its direction
    and a text fragment before any comparison happens. Without it a rule
    can only say "money like this matches invoices like that", and every
    real request (*only for dollars*, *only above ten thousand*, *only
    this client*) is inexpressible.

    All conditions must hold. Deliberately no ANY/OR mode, unlike the
    categorization rules: those pick a label and a wrong guess is a
    mislabelled row, while these bind money to a debt. "The amount matches
    OR the date is close" is a sentence with no safe reading.
    """
    accounts = rule.get("accounts", {}).get("in")
    if accounts and str(movement.account_id) not in {str(a) for a in accounts}:
        return False

    payees = rule.get("payees", {}).get("in")
    if payees and str(movement.payee_id) not in {str(p) for p in payees}:
        return False

    direction = rule.get("direction")
    if direction and direction != "any" and movement.direction != direction:
        return False

    currency = rule.get("currency", {})
    allowed = currency.get("in")
    if allowed and movement.currency not in set(allowed):
        return False
    if currency.get("foreign"):
        # "Foreign" is relative to the workspace, so without knowing the
        # base currency the honest answer is that the rule does not apply,
        # never that everything is foreign.
        if not base_currency or movement.currency == base_currency:
            return False

    amount_rule = rule.get("amount", {})
    moved = abs(movement.amount)
    minimum = amount_rule.get("min")
    if minimum not in (None, "") and moved < Decimal(str(minimum)):
        return False
    maximum = amount_rule.get("max")
    if maximum not in (None, "") and moved > Decimal(str(maximum)):
        return False

    text = rule.get("text", {})
    if text:
        # Both fields, as one haystack. The identifying words land in
        # whichever of the two the bank felt like using, and asking a
        # person which field their bank prefers is asking them to know
        # something about our data model.
        printed = " ".join(
            part.lower()
            for part in (movement.description, movement.counterparty)
            if part
        )
        wanted = _as_list(text.get("contains"))
        # Any of them, not all: somebody receiving through three acquirers
        # wants one rule saying "a payout from any of these", not three
        # rules that differ by a word.
        if wanted and not any(word.lower() in printed for word in wanted):
            return False
        for word in _as_list(text.get("not_contains")):
            if word.lower() in printed:
                return False

    return True


def _as_list(value: Any) -> list[str]:
    """One word or several, read the same way.

    Stored as a list once a rule names alternatives; a plain string is
    still accepted because every rule written before this said one word.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def _eligible_for_set(
    movement: Movement, candidate: Expectation, rule: dict[str, Any]
) -> bool:
    """Whether a promise may take part in a combination at all.

    Every condition except the amount, because the amount is the one being
    asked about the group rather than the member. Narrowing first is also
    what keeps the search tractable: a rule that demands the same client
    usually leaves three or four candidates, not four hundred.
    """
    if candidate.direction != movement.direction:
        return False
    if candidate.amount <= Decimal("0"):
        return False
    if candidate.currency != movement.currency:
        return False
    if rule.get("counterparty") == "same_payee":
        if not movement.payee_id or movement.payee_id != candidate.payee_id:
            return False
    if rule.get("same_account") and movement.account_id != candidate.account_id:
        return False
    if not _within_window(movement.when, candidate.when, rule.get("date", {}), candidate.issued):
        return False
    return True


ZERO = Decimal("0")
#: Money is stored to two places, so a share of it has to land there too.
CENTS = Decimal("0.01")

def _share_out(moved: Decimal, chosen: list[Expectation]) -> list[Settlement]:
    """Split what arrived across the promises it answers.

    The single-candidate path settles `min(what moved, what is owed)`, and
    this is the same rule for a group: a payment can never write more than
    it carried. Without it a tolerance-based group was allowed to settle
    every invoice at its full value, so 980 arriving against two invoices
    of 500 wrote 1.000 of allocations. `allocate` would not catch it: it
    guards each invoice's own balance and knows nothing about how much of
    the transaction earlier members of the group already spent.

    Short payments are shared **in proportion**, because that is what the
    shortfall usually is: a gateway's cut is a percentage of the gross, so
    the larger invoice gives up the larger part of it. The rounding
    residue lands on the last settlement, which keeps the parts summing to
    exactly what moved rather than to a figure a cent away from it.

    A payment larger than the group (interest, a late fee) is not shared
    out at all. Each invoice closes at its value and the surplus stays on
    the transaction, where `difference` names it.
    """
    total = sum((c.amount for c in chosen), Decimal("0"))
    if total <= moved or total <= ZERO:
        return [Settlement(expectation=c, amount=c.amount) for c in chosen]

    settlements: list[Settlement] = []
    spent = Decimal("0")
    for candidate in chosen[:-1]:
        share = (moved * candidate.amount / total).quantize(CENTS, rounding=ROUND_DOWN)
        settlements.append(Settlement(expectation=candidate, amount=share))
        spent += share
    settlements.append(Settlement(expectation=chosen[-1], amount=moved - spent))
    return settlements


def _evaluate_set(
    movement: Movement,
    candidates: list[Expectation],
    strategy: dict[str, Any],
    rule: dict[str, Any],
    policy: dict[str, Any],
    base_currency: Optional[str],
    trace: list[Consideration],
) -> Optional[Decision]:
    """One payment against several promises.

    Returns a decision when this rule has something to say, and nothing
    when it does not, so the strategies after it still get their turn.

    The whole design of the rest of this module carries over unchanged:
    **one answer is a match, more than one is a question.** Three invoices
    of a thousand and a credit of two thousand admit three equally good
    readings, and choosing among them is a person's job.
    """
    amount_rule = rule.get("amount", {})
    eligible = [c for c in candidates if _eligible_for_set(movement, c, rule)]
    moved = abs(movement.amount)

    if len(eligible) < 2:
        return None

    if len(eligible) > MAX_SET_CANDIDATES:
        # Refusing loudly. Silently searching a space this size would make
        # a sync unpredictable, and silently skipping it would leave
        # somebody wondering why their payout never matched.
        trace.append(
            Consideration(
                expectation_id=eligible[0].id,
                strategy=strategy["id"],
                rejected_by=Reason.TOO_MANY_TO_COMBINE,
            )
        )
        return None

    percent = Decimal(str(amount_rule.get("percent", "0")))
    max_size = min(int(amount_rule.get("max_invoices", MAX_SET_SIZE)), MAX_SET_SIZE)

    groups = _combinations_that_add_up(moved, eligible, percent, max_size)
    if not groups:
        for candidate in eligible:
            trace.append(
                Consideration(
                    expectation_id=candidate.id,
                    strategy=strategy["id"],
                    rejected_by=Reason.AMOUNT,
                )
            )
        return None

    outcome = strategy.get("outcome", "suggest")
    if len(groups) > 1:
        # Several combinations fit. Which one is a question about intent,
        # not about arithmetic, so it goes to a person.
        outcome = policy.get("on_ambiguity", "suggest")
        for candidate in eligible:
            trace.append(
                Consideration(
                    expectation_id=candidate.id,
                    strategy=strategy["id"],
                    rejected_by=Reason.AMBIGUOUS_SET,
                )
            )

    chosen = groups[0]
    settlements = _share_out(moved, chosen)
    # The gap is measured against what the invoices are **worth**, not
    # against what was written. Sharing out caps the allocations at what
    # arrived, so comparing with those would report a gap of zero and the
    # gateway's cut would go unnamed: the one number a later deduction
    # has to book.
    promised = sum((c.amount for c in chosen), Decimal("0"))

    for candidate in chosen:
        trace.append(
            Consideration(
                expectation_id=candidate.id, strategy=strategy["id"], score=1.0
            )
        )

    return Decision(
        port="linked" if outcome == "link" else "suggested",
        settlements=settlements,
        expectation=settlements[0].expectation,
        strategy=strategy["id"],
        amount=settlements[0].amount,
        # What the payment carried beyond the invoices it covers: a
        # gateway fee, a bank charge, a rounding. Named rather than
        # swallowed, so the caller can book it instead of leaving money
        # unexplained.
        difference=(moved - promised) if moved != promised else None,
        difference_kind="set_difference" if moved != promised else None,
        score=1.0,
        trace=trace,
    )


def evaluate(
    movement: Movement,
    candidates: list[Expectation],
    policy: dict[str, Any],
    *,
    withholding_ratios: Optional[list[Decimal]] = None,
    base_currency: Optional[str] = None,
    trigger: str = "money_arrives",
) -> Decision:
    """Which expectation this movement settles, if any.

    Strategies are ordered and the first one to match wins; the same
    precedence `rules.priority` already has, so there is one mental model
    for "which rule applied" across the product.

    `base_currency` is what the workspace normally deals in, and is only
    consulted by rules that say "foreign": what counts as foreign is a
    fact about the workspace, not about the money.

    `trigger` says which of the two moments this is (money arriving, or a
    document being written), and rules that were not written for this
    moment sit it out.
    """
    trace: list[Consideration] = []
    ratios = withholding_ratios or []

    ignored = set(policy.get("scope", {}).get("ignore_transaction_sources", []))
    if movement.source and movement.source in ignored:
        # A generated placeholder is a promise, not the money. Matching a
        # promise against a promise would settle an invoice with nothing.
        return Decision(port="unmatched", trace=[])

    for strategy in policy.get("strategies", []):
        if not strategy.get("enabled", True):
            continue
        # Written for the other moment. Looking back at money that was
        # already there is weaker evidence than answering a promise that
        # was waiting, so a rule is allowed to say it only trusts one of
        # them, and saying so on the page beats the same restriction
        # hardcoded into whichever function does the looking.
        wanted = strategy.get("trigger", "money_arrives")
        if wanted != "both" and wanted != trigger:
            trace.append(
                Consideration(
                    expectation_id=candidates[0].id if candidates else uuid.uuid4(),
                    strategy=strategy["id"],
                    rejected_by=Reason.WRONG_MOMENT,
                )
            )
            continue

        rule = strategy.get("when", {})
        if not _in_scope(movement, rule, base_currency):
            # Not written for money like this. One note rather than one per
            # candidate: the rule was never consulted, so there is nothing
            # to say about any particular promise.
            trace.append(
                Consideration(
                    expectation_id=candidates[0].id if candidates else uuid.uuid4(),
                    strategy=strategy["id"],
                    rejected_by=Reason.OUT_OF_SCOPE,
                )
            )
            continue
        # A payment that answers several promises at once. Kept as its own
        # branch because it asks a different question of the candidates:
        # not "which one of you is this" but "which of you, together".
        if rule.get("amount", {}).get("match") == "set":
            decision = _evaluate_set(
                movement, candidates, strategy, rule, policy, base_currency, trace
            )
            if decision is not None:
                return decision
            continue

        matched: list[
            tuple[Expectation, Optional[Decimal], Optional[str], float, Optional[Decimal]]
        ] = []

        for candidate in candidates:
            note = Consideration(expectation_id=candidate.id, strategy=strategy["id"])

            if candidate.direction != movement.direction:
                note.rejected_by = Reason.DIRECTION
                trace.append(note)
                continue

            if candidate.amount <= Decimal("0"):
                note.rejected_by = Reason.ALREADY_SETTLED
                trace.append(note)
                continue

            # Always, and not a setting.
            #
            # There was a `currency.conversion` knob here whose "allow"
            # value read as *convert and compare*. It did no such thing:
            # this module is pure and holds no session, so it cannot look
            # up a rate. All "allow" did was stop comparing currencies,
            # which made a $3.000 payment an **exact** match for a €3.000
            # invoice and settled the euros with the dollars.
            #
            # Matching across currencies needs three things this does not
            # have: a rate, a date to take it on (the invoice's or the
            # bank's, and they differ), and somewhere to put the leftover
            # difference. The third is the same booking problem as
            # withholding, which is why it belongs with deductions rather
            # than here.
            if candidate.currency != movement.currency:
                note.rejected_by = Reason.CURRENCY
                trace.append(note)
                continue

            if rule.get("counterparty") == "same_payee":
                if not movement.payee_id or movement.payee_id != candidate.payee_id:
                    note.rejected_by = Reason.COUNTERPARTY
                    trace.append(note)
                    continue

            if rule.get("same_account") and movement.account_id != candidate.account_id:
                note.rejected_by = Reason.COUNTERPARTY
                trace.append(note)
                continue

            # The inverse, and the defining condition of a transfer:
            # money that left one account and arrived in another. Its own
            # key rather than a value on `same_account`, so that a rule
            # reads as a list of things that must be true and never as a
            # flag whose meaning depends on how it is set.
            if rule.get("different_account") and (
                movement.account_id is None
                or movement.account_id == candidate.account_id
            ):
                note.rejected_by = Reason.SAME_ACCOUNT
                trace.append(note)
                continue

            wanted_types = _as_list(rule.get("account_types"))
            if wanted_types and not (
                movement.account_type in wanted_types
                or candidate.account_type in wanted_types
            ):
                # Either leg is enough. A rule that exists to be careful
                # about credit cards has to fire whichever end the card
                # is on, and asking somebody to write the rule twice
                # would be asking them to know which side we happen to
                # examine first.
                note.rejected_by = Reason.ACCOUNT_TYPE
                trace.append(note)
                continue

            # How strongly the two texts point at each other. **Graded,
            # not a yes or no**, and that is the whole of why the
            # wrong-counterpart case comes out right.
            #
            # Two transfers leave one account on the same day and the
            # incoming line reads `Transfer from BNP`. That names the
            # source, so on its own it fits *both* debits equally and
            # decides nothing. What separates them is that one of the
            # two also reads `To FORTUNEO ACCOUNT`, so that pair names
            # itself from both ends and the other only from one.
            #
            # A boolean would have called both a match and handed the
            # choice back to the tie-break, which has nothing to go on
            # when the dates are identical. Counting the directions puts
            # the better pair in front, where `tie_break` can see it.
            naming: Optional[float] = None
            if rule.get("account_name_in_description"):
                sides = (
                    names_account(movement.description, candidate.account_name)
                    or names_account(movement.counterparty, candidate.account_name),
                    names_account(candidate.description, movement.account_name)
                    or names_account(candidate.counterparty, movement.account_name),
                )
                if not any(sides):
                    note.rejected_by = Reason.ACCOUNT_NOT_NAMED
                    trace.append(note)
                    continue
                naming = sum(1 for side in sides if side) / 2.0

            # Against each whole part in turn, next installment first,
            # so the smallest honest reading wins: a payment of exactly
            # one installment is that installment, not a part of the rest.
            ok, difference, difference_kind, target = False, None, None, None
            for target in candidate.targets():
                ok, difference, difference_kind = _amount_verdict(
                    movement.amount, target, rule.get("amount", {}), ratios
                )
                if ok:
                    break
            if not ok:
                note.rejected_by = Reason.AMOUNT
                trace.append(note)
                continue

            if not _within_window(
                movement.when, candidate.when, rule.get("date", {}), candidate.issued
            ):
                note.rejected_by = Reason.DATE
                trace.append(note)
                continue

            score = 1.0
            similarity = rule.get("description_similarity")
            if similarity:
                score = token_overlap(movement.description, candidate.description)
                if score < float(similarity.get("min", 0)):
                    note.rejected_by = Reason.DESCRIPTION
                    note.score = score
                    trace.append(note)
                    continue
            elif naming is not None:
                # Only when nothing else grades the pair. A rule asking
                # for both would be asking two questions of one number,
                # and no rule does.
                score = naming

            note.score = score
            trace.append(note)
            matched.append((candidate, difference, difference_kind, score, target))

        if not matched:
            continue

        outcome = strategy.get("outcome", "suggest")

        if len(matched) > 1 and rule.get("tie_break") == "closest_date":
            # **Two different meanings of "more than one candidate", and
            # collapsing them loses a signal people rely on.**
            #
            # Transfers are where this shows. Money leaves an account
            # today and two credits of the same value sit in the
            # destination, one today and one two days out: the same-day
            # one is the transfer and the other is somebody paying you
            # back. Refusing both because there were two would throw away
            # the very signal the old detector was built on, and it was
            # right about it.
            #
            # What it was wrong about is the case where nothing separates
            # them. Two debits leaving on the same day for different
            # destinations are equidistant from the credit, and there the
            # answer has to be a question.
            #
            # So this narrows to the candidates tied at the front, and
            # whatever `unique_candidate` says next applies to those. A
            # clear winner is one candidate and links; a genuine tie is
            # still several and does not.
            def _closeness(entry: tuple[Expectation, Any, Any, float, Any]):
                return (-entry[3], abs((movement.when - entry[0].when).days))

            ranked = sorted(matched, key=_closeness)
            front = _closeness(ranked[0])
            tied = [entry for entry in ranked if _closeness(entry) == front]
            if len(tied) < len(matched):
                beaten = {id(entry) for entry in matched} - {id(e) for e in tied}
                for note in trace:
                    if (
                        note.strategy == strategy["id"]
                        and note.rejected_by is None
                        and any(
                            entry[0].id == note.expectation_id
                            for entry in matched
                            if id(entry) in beaten
                        )
                    ):
                        note.rejected_by = Reason.FURTHER_IN_TIME
            matched = tied

        if len(matched) > 1:
            if rule.get("unique_candidate", False):
                # Several answers to a question that admits one. Downgrade
                # rather than guess: picking the highest score here would
                # be inventing certainty the signals do not carry.
                outcome = policy.get("on_ambiguity", "suggest")
                for note in trace:
                    if note.strategy == strategy["id"] and note.rejected_by is None:
                        note.rejected_by = Reason.AMBIGUOUS
            matched.sort(key=lambda m: m[3], reverse=True)

        winner, difference, difference_kind, score, target = matched[0]
        settled = min(abs(movement.amount), winner.amount)
        return Decision(
            port="linked" if outcome == "link" else "suggested",
            # Always a set, even when it holds one. A caller that has to
            # ask "is this the single kind or the several kind" before it
            # can write anything is a caller that will one day forget to.
            settlements=[
                Settlement(
                    expectation=winner,
                    amount=settled,
                    target=target if target != winner.amount else None,
                )
            ],
            expectation=winner,
            strategy=strategy["id"],
            amount=settled,
            difference=difference,
            difference_kind=difference_kind,
            score=score,
            trace=trace,
        )

    return Decision(port="unmatched", trace=trace)
