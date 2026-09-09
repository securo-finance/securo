"""What the matcher decides, and why.

Pure tests: no session, no fixtures, no database. That is the point of
splitting deciding from writing: the half that holds the judgement can
be asked a hundred questions in a second.

The organising claim under all of it: **an invoice and a recurring
occurrence are the same kind of promise**, and the engine scores them
identically. Where they differ is in what the caller does afterwards,
which is not this module's business and not tested here.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import reconciliation_policy as policy_module
from app.services.reconciliation_engine import (
    Expectation,
    Movement,
    Reason,
    evaluate,
)

TODAY = date(2026, 9, 1)
CLIENT = uuid.uuid4()
ACCOUNT = uuid.uuid4()


def receivable() -> dict:
    return policy_module.default_policy("reconciliation.match_invoice")


def an_invoice(
    *,
    amount: Decimal = Decimal("3000.00"),
    currency: str = "BRL",
    direction: str = "credit",
    when: date = TODAY,
    description: str = "Consultoria",
    payee_id: uuid.UUID | None = CLIENT,
    issued: date | None = None,
) -> Expectation:
    """Built field by field rather than from a splatted dict: keyword
    -splatting a heterogeneous mapping loses every type on the way in,
    and these fixtures are what the whole file leans on."""
    return Expectation(
        kind="invoice",
        id=uuid.uuid4(),
        amount=amount,
        currency=currency,
        direction=direction,
        when=when,
        issued=issued,
        description=description,
        payee_id=payee_id,
    )


def an_inflow(
    *,
    amount: Decimal = Decimal("3000.00"),
    currency: str = "BRL",
    direction: str = "credit",
    when: date = TODAY,
    description: str = "PIX RECEBIDO",
    payee_id: uuid.UUID | None = CLIENT,
    source: str = "sync",
) -> Movement:
    return Movement(
        amount=amount,
        currency=currency,
        direction=direction,
        when=when,
        description=description,
        payee_id=payee_id,
        source=source,
    )


# ---------------------------------------------------------------------------
# The tier that should carry the traffic
# ---------------------------------------------------------------------------
def test_a_known_client_paying_the_exact_amount_is_linked_not_suggested():
    """This is a lookup, not a heuristic. The payee is already on the
    transaction (set by a mapping or a rule), so asking a person to
    confirm it would be asking them to confirm what they configured."""
    invoice = an_invoice()
    decision = evaluate(an_inflow(), [invoice], receivable())

    assert decision.port == "linked"
    assert decision.strategy == "same_client_exact"
    assert decision.expectation is invoice
    assert decision.amount == Decimal("3000.00")


def test_money_from_an_unknown_payer_still_links_on_an_exact_amount():
    """A Pix arrives with no payee attached far more often than not."""
    decision = evaluate(an_inflow(payee_id=None), [an_invoice()], receivable())

    assert decision.port == "linked"
    assert decision.strategy == "exact_amount_any_client"


def test_a_near_amount_is_offered_rather_than_taken():
    """Two per cent off is not a rounding difference the product should
    decide about on its own."""
    decision = evaluate(
        an_inflow(amount=Decimal("2950.00"), description="Consultoria"),
        [an_invoice()],
        receivable(),
    )

    assert decision.port == "suggested"
    assert decision.strategy == "similar_description"


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------
def test_two_invoices_for_the_same_amount_are_offered_never_chosen():
    """The signals cannot tell them apart, and picking the higher score
    would be inventing certainty. The user is asked instead."""
    twins = [an_invoice(), an_invoice()]
    decision = evaluate(an_inflow(), twins, receivable())

    assert decision.port == "suggested"
    assert any(n.rejected_by is Reason.AMBIGUOUS for n in decision.trace)


def test_a_different_currency_fails_loudly_instead_of_binding():
    """Binding across currencies means inventing a rate nobody chose."""
    decision = evaluate(
        an_inflow(currency="USD"), [an_invoice(currency="BRL")], receivable()
    )

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.CURRENCY for n in decision.trace)


def test_money_going_the_wrong_way_never_settles_a_receivable():
    decision = evaluate(an_inflow(direction="debit"), [an_invoice()], receivable())

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.DIRECTION for n in decision.trace)


def test_a_generated_placeholder_never_settles_an_invoice():
    """It is a promise, not the money that keeps it. Settling a debt with
    another debt is the failure this scope rule exists for."""
    decision = evaluate(an_inflow(source="recurring"), [an_invoice()], receivable())

    assert decision.port == "unmatched"


def test_an_invoice_with_nothing_left_is_not_a_candidate():
    decision = evaluate(an_inflow(), [an_invoice(amount=Decimal("0"))], receivable())

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.ALREADY_SETTLED for n in decision.trace)


def test_paying_the_day_after_issue_is_not_twenty_nine_days_early():
    """An invoice issued on the 1st and due on the 30th, paid on the 2nd.
    Measured from the due date that is 28 days early and rejected, which
    would reject the best-paying client in the workspace. Early is
    measured from the day the promise was made."""
    invoice = an_invoice(when=TODAY + timedelta(days=29), issued=TODAY)
    decision = evaluate(an_inflow(when=TODAY + timedelta(days=1)), [invoice], receivable())

    assert decision.port == "linked"


def test_money_from_before_the_invoice_existed_still_has_a_limit():
    """The look-back is not unbounded: money from two months before the
    document was written is not this document's."""
    invoice = an_invoice(when=TODAY + timedelta(days=29), issued=TODAY)
    decision = evaluate(an_inflow(when=TODAY - timedelta(days=60)), [invoice], receivable())

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.DATE for n in decision.trace)


def test_the_date_window_is_asymmetric_on_purpose():
    """Money arrives after a due date far more often than before it, so
    the window reaches much further forward than back."""
    late = evaluate(
        an_inflow(when=TODAY + timedelta(days=40)), [an_invoice()], receivable()
    )
    early = evaluate(
        an_inflow(when=TODAY - timedelta(days=40)), [an_invoice()], receivable()
    )

    assert late.port == "linked"
    assert early.port == "unmatched"
    assert any(n.rejected_by is Reason.DATE for n in early.trace)


# ---------------------------------------------------------------------------
# Partial settlement
# ---------------------------------------------------------------------------
def test_a_second_instalment_matches_what_is_left_not_the_original_total():
    """An invoice half paid expects the other half. Matching against the
    full value would miss every second Pix in a market that pays in
    parts."""
    half_settled = an_invoice(amount=Decimal("1500.00"))
    decision = evaluate(an_inflow(amount=Decimal("1500.00")), [half_settled], receivable())

    assert decision.port == "linked"
    assert decision.amount == Decimal("1500.00")


def test_more_money_than_the_invoice_owes_settles_only_what_is_owed():
    """The remainder is not this module's problem (it is an ordinary
    transaction), but the allocation must never exceed the debt."""
    decision = evaluate(
        an_inflow(amount=Decimal("5000.00")),
        [an_invoice(amount=Decimal("3000.00"))],
        {
            **receivable(),
            "strategies": [
                {
                    "id": "loose",
                    "outcome": "link",
                    "when": {
                        "counterparty": "any",
                        "amount": {"match": "tolerance", "percent": "100"},
                        "date": {"before_days": 5, "after_days": 60},
                    },
                }
            ],
        },
    )

    assert decision.amount == Decimal("3000.00")


# ---------------------------------------------------------------------------
# Withholding: the reason the ratio strategy is a link
# ---------------------------------------------------------------------------
def test_an_invoice_paid_net_of_withholding_is_linked_and_the_gap_named():
    """R$3.000 from a Brazilian company lands as R$2.955 after 1.5% IRRF.
    Sending that to a confirmation queue sends the best clients to the
    queue, and the difference is named so the caller can book it rather
    than leaving R$45 unexplained."""
    decision = evaluate(
        an_inflow(amount=Decimal("2955.00")),
        [an_invoice()],
        receivable(),
        withholding_ratios=[Decimal("0.985")],
    )

    assert decision.port == "linked"
    assert decision.strategy == "same_client_net_of_withholding"
    assert decision.difference == Decimal("45.00")
    assert decision.difference_kind == "withholding_tax"


def test_without_ratios_the_withholding_strategy_simply_does_not_fire():
    """A wrong ratio would auto-link a wrong amount, which is worse than
    asking. An empty pack means the strategy is inert, not permissive."""
    decision = evaluate(
        an_inflow(amount=Decimal("2955.00")), [an_invoice()], receivable()
    )

    assert decision.port != "linked"


# ---------------------------------------------------------------------------
# The same engine, the other kind of promise
# ---------------------------------------------------------------------------
def a_recurring_occurrence(
    *,
    amount: Decimal = Decimal("89.90"),
    when: date = TODAY,
    description: str = "NETFLIX ASSINATURA",
    account_id: uuid.UUID = ACCOUNT,
) -> Expectation:
    return Expectation(
        kind="recurring",
        id=uuid.uuid4(),
        amount=amount,
        currency="BRL",
        direction="debit",
        when=when,
        description=description,
        account_id=account_id,
    )


def test_a_recurring_bill_is_matched_by_the_same_engine():
    """A workspace that never issues an invoice still has promises: the
    rent leaving on the 5th is a receivable seen from the other side."""
    charge = Movement(
        amount=Decimal("89.90"), currency="BRL", direction="debit",
        when=TODAY + timedelta(days=2), description="NETFLIX ASSINATURA",
        account_id=ACCOUNT, source="sync",
    )
    decision = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("monthly")
    )

    assert decision.port == "linked"
    assert decision.strategy == "same_account_exact"
    assert decision.expectation is not None
    assert decision.expectation.kind == "recurring"


def test_a_recurring_bill_paid_from_another_account_is_not_a_match():
    """Unlike an invoice, a recurring bill is anchored: the account is
    part of what identifies it."""
    charge = Movement(
        amount=Decimal("89.90"), currency="BRL", direction="debit", when=TODAY,
        description="NETFLIX.COM", account_id=uuid.uuid4(), source="sync",
    )
    decision = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("monthly")
    )

    assert decision.port == "unmatched"


def test_a_weekly_bill_gets_a_narrower_window_so_it_cannot_take_its_neighbour():
    """Four days out is fine for a monthly bill and wrong for a weekly
    one, where it reaches into the next occurrence."""
    charge = Movement(
        amount=Decimal("89.90"), currency="BRL", direction="debit",
        when=TODAY + timedelta(days=4), description="NETFLIX ASSINATURA",
        account_id=ACCOUNT, source="sync",
    )

    monthly = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("monthly")
    )
    weekly = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("weekly")
    )

    assert monthly.port == "linked"
    assert weekly.port == "unmatched"


def test_an_unrelated_charge_of_the_same_amount_is_refused_on_description():
    """The amount alone is not enough for a recurring bill: two
    subscriptions at R$89,90 on one account are ordinary."""
    charge = Movement(
        amount=Decimal("89.90"), currency="BRL", direction="debit", when=TODAY,
        description="POSTO IPIRANGA", account_id=ACCOUNT, source="sync",
    )
    decision = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("monthly")
    )

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.DESCRIPTION for n in decision.trace)


# ---------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------
def test_every_candidate_leaves_a_reason_behind():
    """"No strategy matched" tells a person nothing. "The amount was
    right and the date was outside the window" tells them what to
    change."""
    decision = evaluate(
        an_inflow(when=TODAY - timedelta(days=90)), [an_invoice()], receivable()
    )

    assert decision.port == "unmatched"
    assert decision.trace, "a rejected candidate must still be accounted for"
    assert all(n.rejected_by is not None for n in decision.trace)
    # Every strategy that got as far as the date failed on it, which is
    # the signal a person would need to see.
    assert Reason.DATE in {n.rejected_by for n in decision.trace}


def test_the_winner_carries_its_score():
    """A caller holding several movements for one promise ranks them by
    this: the recurring matcher does, and has always taken the better
    -matching charge rather than refusing both. It is also what a
    suggestion has to show a person to be worth showing."""
    charge = Movement(
        amount=Decimal("89.90"), currency="BRL", direction="debit", when=TODAY,
        description="NETFLIX ASSINATURA", account_id=ACCOUNT, source="sync",
    )
    exact = evaluate(
        charge, [a_recurring_occurrence()], policy_module.for_recurring("monthly")
    )
    partial_words = evaluate(
        charge,
        [a_recurring_occurrence(description="NETFLIX ASSINATURA MENSAL")],
        policy_module.for_recurring("monthly"),
    )

    assert exact.score == 1.0
    assert 0.6 <= partial_words.score < 1.0

    # A strategy with no graded signal still reports a usable score rather
    # than a zero that would sort below every graded match.
    assert evaluate(an_inflow(), [an_invoice()], receivable()).score == 1.0
    # And nothing decided scores nothing, so it never sorts above a match.
    assert evaluate(an_inflow(currency="USD"), [an_invoice()], receivable()).score == 0.0


def test_the_winning_strategy_is_named():
    """What lets the screen answer "why is this linked" with the name of
    the rule rather than a shrug."""
    decision = evaluate(an_inflow(), [an_invoice()], receivable())

    assert decision.strategy == "same_client_exact"
    assert any(
        n.strategy == "same_client_exact" and n.rejected_by is None
        for n in decision.trace
    )


def test_an_unknown_node_is_refused_rather_than_silently_empty():
    with pytest.raises(ValueError, match="Unknown reconciliation node"):
        policy_module.default_policy("reconciliation.does_not_exist")
