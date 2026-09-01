"""Deciding which expectation a movement of money settles.

**This module decides and never writes.** It touches no session, loads
nothing, and returns a decision plus the reasoning that produced it.
Applying that decision — creating an allocation, or upgrading a
placeholder in place — belongs to the caller, and is deliberately a
separate step: a function that mutates cannot be dry-run, and "show me
what would happen before it happens" is the whole point of putting this
in front of a person.

## What an expectation is

An invoice and a scheduled recurring occurrence are the same thing seen
from here: **a promise that money will move, waiting for the movement
that confirms it.** A workspace that never issues an invoice still has
promises — the rent leaving on the 5th, the retainer arriving on the
20th — and they want matching for exactly the same reason.

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
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

#: What the decision came out of. Named rather than boolean so a node
#: graph can wire each one somewhere different later.
Port = Literal["linked", "suggested", "unmatched"]

#: Which kind of promise a candidate is. The engine treats them alike;
#: the caller uses this to pick how to apply the decision.
ExpectationKind = Literal["invoice", "recurring"]


class Reason(str, Enum):
    """Why a strategy did not fire, in words a UI can show.

    A rejected candidate is more useful than a silent one: "no strategy
    matched" tells a person nothing, while "the amount was right and the
    date was eleven days outside the window" tells them what to change.
    """

    DIRECTION = "direction_differs"
    CURRENCY = "currency_differs"
    COUNTERPARTY = "different_counterparty"
    AMOUNT = "amount_outside_tolerance"
    DATE = "date_outside_window"
    DESCRIPTION = "description_too_different"
    ALREADY_SETTLED = "nothing_left_to_settle"
    AMBIGUOUS = "several_candidates_matched"
    SOURCE_IGNORED = "transaction_source_ignored"


@dataclass(frozen=True)
class Movement:
    """The money that actually moved, reduced to what matching needs.

    Built from a `Transaction` by the caller. A value object rather than
    the ORM row so this module stays pure and trivially testable, and so
    a future intake that is not a `Transaction` — a gateway payout line,
    say — can be scored without pretending to be one.
    """

    amount: Decimal
    currency: str
    #: `credit` (money in) or `debit` (money out).
    direction: str
    when: date
    description: Optional[str] = None
    payee_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    #: `sync`, `ofx`, `csv`, `manual`, `recurring`. A policy may ignore
    #: some — the receivable node ignores `recurring`, because a
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
    payee_id: Optional[uuid.UUID] = None
    #: Set for a recurring bill, which is charged to a known account.
    #: Null for an invoice, where the money may land anywhere.
    account_id: Optional[uuid.UUID] = None
    #: When the promise came into existence, if that is a different day
    #: from when it comes due. An invoice has both and they are weeks
    #: apart; a recurring occurrence has one, and leaves this null.
    issued: Optional[date] = None


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
    expectation: Optional[Expectation] = None
    strategy: Optional[str] = None
    amount: Optional[Decimal] = None
    #: Set when a strategy matched a known fraction rather than the whole
    #: — Brazilian withholding is the reason this exists. The caller
    #: books the difference; the engine only names it.
    difference: Optional[Decimal] = None
    difference_kind: Optional[str] = None
    #: How well the winner scored, 1.0 when the strategy carries no
    #: graded signal. A caller holding several movements for one
    #: expectation ranks them by this — the recurring matcher does, and
    #: has always taken the better-matching charge rather than refusing
    #: both. Exposed rather than dug out of the trace because it is also
    #: what a suggestion has to show a person to be worth showing. Zero
    #: when nothing was decided, so an unmatched result never sorts above
    #: a real one.
    score: float = 0.0
    trace: list[Consideration] = field(default_factory=list)


def _similar(a: Optional[str], b: Optional[str]) -> float:
    """Description similarity, 0..1.

    Token overlap over the longer side — the measure
    `recurring_match_service` has run in production since issue #116, and
    the same bar the bank-sync fuzzy merge tunes against. Reproduced
    rather than improved on purpose: this lands under thousands of
    existing recurring bills, and a matcher that starts finding pairs it
    used to miss is a behaviour change nobody asked for on the day it
    ships.

    It is deliberately unforgiving. "NETFLIX.COM" against "NETFLIX
    ASSINATURA" scores zero, because a bank string and a hand-typed one
    rarely share tokens exactly — which is why the exact-amount signal
    carries the weight and this only guards against two bills of the same
    value on one account. A looser measure belongs in the policy as a
    knob, next to the threshold, not baked in here.
    """
    if not a or not b:
        return 0.0
    tokens_a = {t for t in a.lower().split() if t}
    tokens_b = {t for t in b.lower().split() if t}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


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
    early" when the client pays on the 2nd — it is paid the day after it
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


def evaluate(
    movement: Movement,
    candidates: list[Expectation],
    policy: dict[str, Any],
    *,
    withholding_ratios: Optional[list[Decimal]] = None,
) -> Decision:
    """Which expectation this movement settles, if any.

    Strategies are ordered and the first one to match wins — the same
    precedence `rules.priority` already has, so there is one mental model
    for "which rule applied" across the product.
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
        rule = strategy.get("when", {})
        matched: list[tuple[Expectation, Optional[Decimal], Optional[str], float]] = []

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

            if rule.get("currency", {}).get("conversion", "reject") == "reject":
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

            ok, difference, difference_kind = _amount_verdict(
                movement.amount, candidate.amount, rule.get("amount", {}), ratios
            )
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
                score = _similar(movement.description, candidate.description)
                if score < float(similarity.get("min", 0)):
                    note.rejected_by = Reason.DESCRIPTION
                    note.score = score
                    trace.append(note)
                    continue

            note.score = score
            trace.append(note)
            matched.append((candidate, difference, difference_kind, score))

        if not matched:
            continue

        outcome = strategy.get("outcome", "suggest")
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

        winner, difference, difference_kind, score = matched[0]
        settled = min(abs(movement.amount), winner.amount)
        return Decision(
            port="linked" if outcome == "link" else "suggested",
            expectation=winner,
            strategy=strategy["id"],
            amount=settled,
            difference=difference,
            difference_kind=difference_kind,
            score=score,
            trace=trace,
        )

    return Decision(port="unmatched", trace=trace)
