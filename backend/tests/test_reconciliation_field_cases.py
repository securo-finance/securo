"""The situations people actually reconcile, taken from the field.

Not a second grammar test: `test_reconciliation_conditions.py` covers what
a rule may say. This file takes reconciliation cases that accountants and
owners describe as their real work, and shows the shipped vocabulary
either handles each one or does not. Every case names where it came from,
so a reader can check that it is a real problem rather than one we
invented to have an answer for.

The cases are deliberately mundane. A matching engine that only shines on
exotic inputs is of no use to somebody closing a month.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.services.reconciliation_engine import (
    Expectation,
    Movement,
    Reason,
    evaluate,
)

TODAY = date(2026, 9, 1)
CLIENT = uuid.uuid4()
OTHER_CLIENT = uuid.uuid4()
ACCOUNT = uuid.uuid4()
PERSONAL = uuid.uuid4()


def invoice(
    *,
    amount: str = "1000.00",
    when: date = TODAY,
    payee: uuid.UUID | None = CLIENT,
    currency: str = "BRL",
    direction: str = "credit",
) -> Expectation:
    return Expectation(
        kind="invoice",
        id=uuid.uuid4(),
        amount=Decimal(amount),
        currency=currency,
        direction=direction,
        when=when,
        description="Consultoria",
        payee_id=payee,
        issued=when - timedelta(days=30),
    )


def money(
    *,
    amount: str = "1000.00",
    when: date = TODAY,
    payee: uuid.UUID | None = CLIENT,
    description: str = "PIX RECEBIDO",
    counterparty: str | None = None,
    account: uuid.UUID | None = ACCOUNT,
    currency: str = "BRL",
    direction: str = "credit",
) -> Movement:
    return Movement(
        amount=Decimal(amount),
        currency=currency,
        direction=direction,
        when=when,
        description=description,
        counterparty=counterparty,
        payee_id=payee,
        account_id=account,
        source="sync",
    )


def rule(when: dict, outcome: str = "link") -> dict:
    """A policy holding the one rule under test."""
    return {
        "version": 1,
        "node": "test",
        "scope": {"movement": "any", "ignore_transaction_sources": []},
        "strategies": [
            {"id": "under_test", "enabled": True, "outcome": outcome, "when": when}
        ],
        "on_ambiguity": "suggest",
    }


WINDOW = {"before_days": 10, "after_days": 60}


# ---------------------------------------------------------------------------
# One payment, several promises
# ---------------------------------------------------------------------------
def test_one_deposit_settles_five_invoices():
    """The case every reconciliation vendor leads with: a retailer sends
    one transfer covering five invoices, the books hold five receivables,
    the bank holds one line.
    """
    invoices = [invoice(amount=a) for a in ("2000", "3200", "1800", "4000", "1400")]
    total = sum(Decimal(str(i.amount)) for i in invoices)
    assert total == Decimal("12400")

    decision = evaluate(
        money(amount="12400.00"),
        invoices,
        rule({
            "counterparty": "same_payee",
            "amount": {"match": "set", "max_invoices": 6, "percent": "0"},
            "date": WINDOW,
        }),
    )

    assert decision.port == "linked"
    assert len(decision.settlements) == 5
    assert sum(s.amount for s in decision.settlements) == Decimal("12400")


def test_a_gateway_payout_arrives_net_of_its_cut():
    """The retail case Brazilian accountants describe as the hard one: the
    acquirer deposits the day's sales minus its fee, so the deposit never
    equals the sum of the invoices it pays.
    """
    invoices = [invoice(amount="1000.00"), invoice(amount="1000.00")]
    # 3.5% of 2.000 kept by the acquirer.
    decision = evaluate(
        money(amount="1930.00", description="REPASSE ADQUIRENTE"),
        invoices,
        rule({
            "counterparty": "same_payee",
            "amount": {"match": "set", "max_invoices": 6, "percent": "4"},
            "date": WINDOW,
        }),
    )

    assert decision.port == "linked"
    assert len(decision.settlements) == 2


# ---------------------------------------------------------------------------
# Several payments, one promise
# ---------------------------------------------------------------------------
def test_a_client_paying_in_two_transfers():
    """Decoding messy partial payments is named as the recurring forum
    complaint. The first half has to be proposable, and every mode that
    compares against the whole balance cannot propose it.
    """
    decision = evaluate(
        money(amount="400.00"),
        [invoice(amount="1000.00")],
        rule(
            {
                "counterparty": "same_payee",
                "amount": {"match": "partial", "min_ratio": "0.05", "max_ratio": "0.95"},
                "date": WINDOW,
            },
            outcome="suggest",
        ),
    )

    assert decision.port == "suggested"
    assert decision.settlements[0].amount == Decimal("400.00")


def test_the_remainder_matches_what_is_left_not_the_original_total():
    """After the first 400, the invoice expects 600, not 1.000. An engine
    comparing against the original total would miss every second
    instalment."""
    decision = evaluate(
        money(amount="600.00"),
        [invoice(amount="600.00")],  # what is still outstanding
        rule({
            "counterparty": "same_payee",
            "amount": {"match": "exact"},
            "date": WINDOW,
        }),
    )

    assert decision.port == "linked"
    assert decision.settlements[0].amount == Decimal("600.00")


def test_a_token_amount_is_not_offered_as_an_instalment():
    """Ten reais against a thousand-real invoice is noise. Offering it
    would teach people to ignore the queue, which costs more than the
    match is worth."""
    decision = evaluate(
        money(amount="10.00"),
        [invoice(amount="1000.00")],
        rule(
            {
                "counterparty": "same_payee",
                "amount": {"match": "partial", "min_ratio": "0.05"},
                "date": WINDOW,
            },
            outcome="suggest",
        ),
    )

    assert decision.port == "unmatched"


# ---------------------------------------------------------------------------
# The money is not the number on the document
# ---------------------------------------------------------------------------
def test_a_late_boleto_paid_with_interest_and_penalty():
    """The ordinary Brazilian case, and the one a symmetric tolerance has
    to get right: the client pays *more* than the invoice because the
    boleto carried juros and multa.

    The invoice closes at its own value and the surplus stays on the
    transaction, which is where interest income belongs. Settling 1.023
    against a 1.000 invoice would invent revenue on the receivable.
    """
    decision = evaluate(
        money(amount="1023.40", when=TODAY + timedelta(days=12)),
        [invoice(amount="1000.00")],
        rule({
            "counterparty": "same_payee",
            "amount": {"match": "tolerance", "percent": "5"},
            "date": WINDOW,
        }),
    )

    assert decision.port == "linked"
    assert decision.settlements[0].amount == Decimal("1000.00"), (
        "the invoice closes at its value; the extra 23,40 is interest income"
    )


def test_an_early_payment_discount():
    """The mirror case: paid before the due date, so the client took the
    agreed discount and sent less."""
    decision = evaluate(
        money(amount="980.00", when=TODAY - timedelta(days=5)),
        [invoice(amount="1000.00")],
        rule({
            "counterparty": "same_payee",
            "amount": {"match": "tolerance", "percent": "3"},
            "date": WINDOW,
        }),
    )

    assert decision.port == "linked"


def test_a_receivables_advance_arrives_smaller_and_sooner():
    """Antecipação de recebíveis: the acquirer pays early and keeps a
    financing fee, so the money is both lighter and earlier than the
    document says.
    """
    decision = evaluate(
        money(amount="1955.00", when=TODAY - timedelta(days=25), description="ANTECIP"),
        [invoice(amount="2000.00", when=TODAY)],
        rule(
            {
                "counterparty": "same_payee",
                "amount": {"match": "tolerance", "percent": "3"},
                "date": {"before_days": 30, "after_days": 5},
            },
            outcome="suggest",
        ),
    )

    assert decision.port == "suggested"


# ---------------------------------------------------------------------------
# Timing, text and scope
# ---------------------------------------------------------------------------
def test_a_deposit_in_transit_still_matches():
    """Settlement delay is the classic reconciling item: the money left
    the client days before it reached the account.
    """
    decision = evaluate(
        money(amount="1000.00", when=TODAY + timedelta(days=45)),
        [invoice(amount="1000.00")],
        rule({"counterparty": "same_payee", "amount": {"match": "exact"}, "date": WINDOW}),
    )

    assert decision.port == "linked"


def test_a_rule_written_against_the_statement_text():
    """The commonest rule people write anywhere: the payout is recognised
    by the words the bank prints, not by its amount.
    """
    conditions = {
        "amount": {"match": "exact"},
        "date": WINDOW,
        "text": {"contains": "REPASSE ADQUIRENTE"},
    }

    assert evaluate(
        money(amount="1000.00", description="REPASSE ADQUIRENTE 8812"),
        [invoice(amount="1000.00")],
        rule(conditions),
    ).port == "linked"

    assert evaluate(
        money(amount="1000.00", description="TED RECEBIDA"),
        [invoice(amount="1000.00")],
        rule(conditions),
    ).port == "unmatched"


def test_text_that_must_not_appear_keeps_a_reversal_out():
    """The other half of the same condition, and the one that prevents a
    refund from closing the invoice it refunded."""
    decision = evaluate(
        money(amount="1000.00", description="ESTORNO PIX CONSULTORIA"),
        [invoice(amount="1000.00")],
        rule({
            "amount": {"match": "exact"},
            "date": WINDOW,
            "text": {"not_contains": "ESTORNO"},
        }),
    )

    assert decision.port == "unmatched"


def test_a_ceiling_sends_the_big_ones_to_a_person():
    """The single most requested guardrail in the rule tools: small
    payments settle themselves, large ones get looked at.
    """
    conditions = {
        "counterparty": "same_payee",
        "amount": {"match": "exact", "max": "10000"},
        "date": WINDOW,
    }

    assert evaluate(
        money(amount="9000.00"), [invoice(amount="9000.00")], rule(conditions)
    ).port == "linked"

    big = evaluate(
        money(amount="50000.00"), [invoice(amount="50000.00")], rule(conditions)
    )
    assert big.port == "unmatched"
    assert any(n.rejected_by is Reason.OUT_OF_SCOPE for n in big.trace)


def test_the_personal_account_is_left_out_of_it():
    """Mixing the personal and the company account is named as the thing
    that makes reconciliation impossible. A rule can be told which
    accounts are the company's.
    """
    conditions = {
        "counterparty": "same_payee",
        "amount": {"match": "exact"},
        "date": WINDOW,
        "accounts": {"in": [str(ACCOUNT)]},
    }

    assert evaluate(
        money(amount="1000.00", account=ACCOUNT),
        [invoice(amount="1000.00")],
        rule(conditions),
    ).port == "linked"

    assert evaluate(
        money(amount="1000.00", account=PERSONAL),
        [invoice(amount="1000.00")],
        rule(conditions),
    ).port == "unmatched"


# ---------------------------------------------------------------------------
# Money that must never settle anything
# ---------------------------------------------------------------------------
def test_a_second_payment_of_the_same_boleto_finds_nothing_left():
    """Paying the same boleto twice is common enough that every Brazilian
    processor documents the refund path. What matters here is that the
    duplicate does not settle a second invoice by accident: the first one
    is closed, and nothing else is owed.
    """
    settled = invoice(amount="0.00")  # nothing outstanding after the first payment
    decision = evaluate(
        money(amount="1000.00"),
        [settled],
        rule({"counterparty": "same_payee", "amount": {"match": "exact"}, "date": WINDOW}),
    )

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.ALREADY_SETTLED for n in decision.trace)


def test_a_refund_leaving_the_account_never_settles_a_receivable():
    """A chargeback or estorno is money going the other way. It reverses a
    settlement rather than making one.
    """
    decision = evaluate(
        money(amount="1000.00", direction="debit", description="ESTORNO"),
        [invoice(amount="1000.00")],
        rule({"amount": {"match": "exact"}, "date": WINDOW}),
    )

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.DIRECTION for n in decision.trace)


def test_another_clients_money_never_closes_this_clients_invoice():
    decision = evaluate(
        money(amount="1000.00", payee=OTHER_CLIENT),
        [invoice(amount="1000.00", payee=CLIENT)],
        rule({"counterparty": "same_payee", "amount": {"match": "exact"}, "date": WINDOW}),
    )

    assert decision.port == "unmatched"
    assert any(n.rejected_by is Reason.COUNTERPARTY for n in decision.trace)


# ---------------------------------------------------------------------------
# What the vocabulary could not say, until it could
# ---------------------------------------------------------------------------
def test_a_rule_can_read_the_name_the_bank_printed():
    """A Pix description is generic ("PIX RECEBIDO"); the payer's name is
    a separate field on the statement. A rule that can only read the
    description cannot say "the transfers that come from this company",
    which is the identifying fact on the most common inflow in Brazil.
    """
    decision = evaluate(
        money(amount="1000.00", description="PIX RECEBIDO", counterparty="ALPHA LTDA"),
        [invoice(amount="1000.00", payee=None)],
        rule({
            "amount": {"match": "exact"},
            "date": WINDOW,
            "text": {"contains": "ALPHA"},
        }),
    )

    assert decision.port == "linked"


def test_one_rule_can_name_several_gateways():
    """Somebody receiving through three acquirers wants one rule that says
    "a payout from any of them", not three rules that differ by a word.
    Letting a rule match on any of its conditions is standard in the rule
    tools; this is the same need at the level of one field.
    """
    conditions = {
        "amount": {"match": "exact"},
        "date": WINDOW,
        "text": {"contains": ["REPASSE", "LIQUIDACAO", "SETTLEMENT"]},
    }

    for printed in ("REPASSE 8812", "LIQUIDACAO DIARIA", "SETTLEMENT 0042"):
        assert evaluate(
            money(amount="1000.00", description=printed),
            [invoice(amount="1000.00", payee=None)],
            rule(conditions),
        ).port == "linked", printed

    assert evaluate(
        money(amount="1000.00", description="TED RECEBIDA"),
        [invoice(amount="1000.00", payee=None)],
        rule(conditions),
    ).port == "unmatched"
