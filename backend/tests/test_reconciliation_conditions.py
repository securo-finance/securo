"""Every condition a rule can carry, and what it does to a real match.

Pure tests, no database: the engine decides and never writes, which is
what lets this file ask several hundred questions in a fraction of a
second. Reconciliation binds money to debts, so the bar here is not "the
happy path works": it is that each condition is exercised on **both**
sides of its boundary, and that combinations behave the way the sentence
on screen says they do.

Organised by the question each condition answers:

  - *Does this rule apply to money like this?*: account, payee list,
    direction, currency, amount band, statement text. These decide
    whether the rule is consulted at all.
  - *Does this money answer this promise?*: amount comparison, dates,
    description similarity, counterparty. These decide the pair.

The distinction matters because the two fail differently, and a person
reading a trace needs to know which happened: "no rule was written for
money like this" and "the rule looked and said no" are different problems
with different fixes.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import reconciliation_policy as policy_module
from app.services import reconciliation_rule_service as rule_service
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
OTHER_ACCOUNT = uuid.uuid4()


def an_invoice(
    *,
    amount: Decimal = Decimal("3000.00"),
    currency: str = "BRL",
    direction: str = "credit",
    when: date = TODAY,
    issued: date | None = None,
    description: str = "Consultoria",
    payee_id: uuid.UUID | None = CLIENT,
) -> Expectation:
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


def money(
    *,
    amount: Decimal = Decimal("3000.00"),
    currency: str = "BRL",
    direction: str = "credit",
    when: date = TODAY,
    description: str | None = "PIX RECEBIDO",
    payee_id: uuid.UUID | None = CLIENT,
    account_id: uuid.UUID | None = ACCOUNT,
    source: str = "sync",
) -> Movement:
    return Movement(
        amount=amount,
        currency=currency,
        direction=direction,
        when=when,
        description=description,
        payee_id=payee_id,
        account_id=account_id,
        source=source,
    )


def one_rule(when: dict, outcome: str = "link") -> dict:
    """A policy holding a single rule, so a test isolates one condition.

    Built rather than taken from the shipped document on purpose: a test
    that leans on the defaults starts failing when the defaults improve,
    which is the opposite of what these are for.
    """
    return {
        "version": 1,
        "node": "test",
        "scope": {"movement": "any", "ignore_transaction_sources": []},
        "strategies": [
            {"id": "under_test", "enabled": True, "outcome": outcome, "when": when}
        ],
        "on_ambiguity": "suggest",
    }


BASE = {
    "counterparty": "any",
    "amount": {"match": "exact"},
    "date": {"before_days": 30, "after_days": 30},
}


def with_scope(**extra) -> dict:
    return {**BASE, **extra}


# ===========================================================================
# Does this rule apply to money like this?
# ===========================================================================
class TestAccountScope:
    """"Only for this bank account."

    The rule tools make this the first choice on every rule, and the reason
    is mundane: a business with a payments account and an operating
    account does not want one account's automation reaching into the
    other's statement.
    """

    def test_money_in_a_named_account_is_matched(self):
        rule = with_scope(accounts={"in": [str(ACCOUNT)]})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    def test_money_in_any_other_account_is_left_alone(self):
        rule = with_scope(accounts={"in": [str(ACCOUNT)]})
        decision = evaluate(
            money(account_id=OTHER_ACCOUNT), [an_invoice()], one_rule(rule)
        )
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_several_accounts_can_share_one_rule(self):
        rule = with_scope(accounts={"in": [str(ACCOUNT), str(OTHER_ACCOUNT)]})
        for account in (ACCOUNT, OTHER_ACCOUNT):
            assert (
                evaluate(
                    money(account_id=account), [an_invoice()], one_rule(rule)
                ).port
                == "linked"
            )

    def test_money_with_no_account_does_not_slip_through_an_account_rule(self):
        """A rule naming accounts must not treat "no account" as "every
        account". Failing narrow is the safe direction when the thing
        being decided is whether money moves."""
        rule = with_scope(accounts={"in": [str(ACCOUNT)]})
        assert (
            evaluate(money(account_id=None), [an_invoice()], one_rule(rule)).port
            == "unmatched"
        )

    def test_no_account_condition_means_every_account(self):
        assert (
            evaluate(
                money(account_id=OTHER_ACCOUNT), [an_invoice()], one_rule(BASE)
            ).port
            == "linked"
        )


class TestPayeeScope:
    """"Only for these clients."

    Distinct from `counterparty: same_payee`, which asks whether the payer
    is *the one on the invoice*. This names specific clients: the ask
    behind "this customer always pays short, never auto-link them".
    """

    def test_a_named_client_is_matched(self):
        rule = with_scope(payees={"in": [str(CLIENT)]})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    def test_another_client_is_left_alone(self):
        rule = with_scope(payees={"in": [str(OTHER_CLIENT)]})
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_an_unidentified_payer_is_not_one_of_the_named_clients(self):
        rule = with_scope(payees={"in": [str(CLIENT)]})
        assert (
            evaluate(money(payee_id=None), [an_invoice()], one_rule(rule)).port
            == "unmatched"
        )


class TestDirectionScope:
    """"Only money going out", or only money coming in.

    The rule tools ask this before anything else. Here the expectation
    already implies a direction, so this is the narrower statement: a rule
    that exists only for payables, even though invoices come in both
    kinds.
    """

    def test_an_outflow_rule_ignores_incoming_money(self):
        rule = with_scope(direction="debit")
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_an_outflow_rule_matches_a_payable(self):
        rule = with_scope(direction="debit")
        decision = evaluate(
            money(direction="debit"),
            [an_invoice(direction="debit")],
            one_rule(rule),
        )
        assert decision.port == "linked"

    def test_any_is_the_same_as_saying_nothing(self):
        assert (
            evaluate(money(), [an_invoice()], one_rule(with_scope(direction="any"))).port
            == "linked"
        )


class TestCurrencyScope:
    """"Only in dollars", or "only in a currency that is not ours".

    The second is the one somebody actually asks for: a workspace keeping
    books in reais wants euro receipts looked at by a person, without
    having to name every currency the world might send.
    """

    def test_a_named_currency_matches(self):
        rule = with_scope(currency={"conversion": "reject", "in": ["USD"]})
        decision = evaluate(
            money(currency="USD"), [an_invoice(currency="USD")], one_rule(rule)
        )
        assert decision.port == "linked"

    def test_a_currency_not_on_the_list_is_out_of_scope(self):
        rule = with_scope(currency={"conversion": "reject", "in": ["USD"]})
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_currency_codes_are_compared_as_written(self):
        """Stored uppercase by the validator, so the engine can compare
        plainly rather than guessing at case on every transaction."""
        rule = with_scope(currency={"conversion": "reject", "in": ["USD", "EUR"]})
        assert (
            evaluate(
                money(currency="EUR"), [an_invoice(currency="EUR")], one_rule(rule)
            ).port
            == "linked"
        )

    def test_foreign_means_not_what_this_workspace_deals_in(self):
        rule = with_scope(currency={"conversion": "reject", "foreign": True})
        decision = evaluate(
            money(currency="USD"),
            [an_invoice(currency="USD")],
            one_rule(rule),
            base_currency="BRL",
        )
        assert decision.port == "linked"

    def test_the_home_currency_is_not_foreign(self):
        rule = with_scope(currency={"conversion": "reject", "foreign": True})
        decision = evaluate(
            money(), [an_invoice()], one_rule(rule), base_currency="BRL"
        )
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_without_a_home_currency_a_foreign_rule_does_nothing(self):
        """Rather than treating everything as foreign. A rule that fires on
        everything because we failed to look something up is worse than a
        rule that fires on nothing."""
        rule = with_scope(currency={"conversion": "reject", "foreign": True})
        assert (
            evaluate(money(currency="USD"), [an_invoice(currency="USD")], one_rule(rule)).port
            == "unmatched"
        )

    def test_naming_a_currency_never_lets_the_pair_disagree(self):
        """`in` says which money the rule looks at at all. It is not
        permission for the two sides to be different money.

        This test used to assert the opposite, because a
        `conversion: allow` knob read as *convert and compare* and did
        nothing of the sort: it settled a Brazilian invoice with dollars
        at face value, and this test pinned that as a feature.
        """
        rule = with_scope(currency={"in": ["USD"]})
        assert (
            evaluate(
                money(currency="USD"), [an_invoice(currency="BRL")], one_rule(rule)
            ).port
            == "unmatched"
        )
        assert (
            evaluate(
                money(currency="USD"), [an_invoice(currency="USD")], one_rule(rule)
            ).port
            == "linked"
        )


class TestAmountBand:
    """"Only under ten thousand", or "only above five hundred".

    The amount operators every rule tool offers, and the single most
    requested guardrail:
    small payments settle themselves, large ones get a human. It is a
    filter on the money, not a comparison with the promise: a rule can
    demand an exact match *and* refuse to act above a ceiling.
    """

    def test_money_inside_the_band_matches(self):
        rule = with_scope(amount={"match": "exact", "min": "100", "max": "5000"})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    def test_money_above_the_ceiling_is_left_for_a_person(self):
        rule = with_scope(amount={"match": "exact", "max": "1000"})
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_money_below_the_floor_is_left_alone(self):
        rule = with_scope(amount={"match": "exact", "min": "5000"})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "unmatched"

    def test_the_boundaries_are_inclusive(self):
        """Somebody who writes "up to 3000" means 3000 is allowed. Off by
        one here is a payment that silently stops being matched."""
        exact = with_scope(amount={"match": "exact", "min": "3000", "max": "3000"})
        assert evaluate(money(), [an_invoice()], one_rule(exact)).port == "linked"

    def test_a_band_is_read_on_the_size_of_the_movement_not_its_sign(self):
        """An outflow of 3000 is three thousand of money, not minus three
        thousand: otherwise every band would need writing twice."""
        rule = with_scope(
            direction="debit", amount={"match": "exact", "min": "1000", "max": "5000"}
        )
        decision = evaluate(
            money(amount=Decimal("-3000.00"), direction="debit"),
            [an_invoice(direction="debit")],
            one_rule(rule),
        )
        assert decision.port == "linked"

    def test_a_band_and_a_tolerance_are_different_questions(self):
        """The band says the rule applies; the tolerance says how close the
        pair must be. Both hold at once."""
        rule = with_scope(
            amount={"match": "tolerance", "percent": "5", "min": "1000"}
        )
        near = evaluate(
            money(amount=Decimal("2950.00")), [an_invoice()], one_rule(rule)
        )
        assert near.port == "linked"

        below_band = evaluate(
            money(amount=Decimal("500.00")),
            [an_invoice(amount=Decimal("500.00"))],
            one_rule(rule),
        )
        assert below_band.port == "unmatched"


class TestStatementText:
    """"Only when the statement says PIX", or "never when it says ESTORNO".

    Bank text is where a payment's real identity hides, which is why every
    rule tool puts it front and centre. It is also the bridge to
    something we cannot yet read structurally: a client whose transfers
    always carry their tax number can be recognised by that number today,
    typed in as a fragment.
    """

    def test_a_required_fragment_matches(self):
        rule = with_scope(text={"contains": "PIX"})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    def test_a_missing_fragment_puts_the_rule_out_of_scope(self):
        rule = with_scope(text={"contains": "TED"})
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.OUT_OF_SCOPE

    def test_matching_ignores_case(self):
        """Banks shout. Nobody should have to guess whether their
        statement writes PIX or Pix."""
        rule = with_scope(text={"contains": "pix recebido"})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    def test_an_excluded_fragment_holds_the_rule_back(self):
        rule = with_scope(text={"not_contains": "ESTORNO"})
        decision = evaluate(
            money(description="PIX ESTORNO CLIENTE"), [an_invoice()], one_rule(rule)
        )
        assert decision.port == "unmatched"

    def test_include_and_exclude_work_together(self):
        rule = with_scope(text={"contains": "PIX", "not_contains": "ESTORNO"})
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"
        assert (
            evaluate(
                money(description="PIX ESTORNO"), [an_invoice()], one_rule(rule)
            ).port
            == "unmatched"
        )

    def test_a_movement_with_no_description_fails_a_required_fragment(self):
        rule = with_scope(text={"contains": "PIX"})
        assert (
            evaluate(money(description=None), [an_invoice()], one_rule(rule)).port
            == "unmatched"
        )

    def test_a_movement_with_no_description_passes_an_exclusion(self):
        """Nothing was said, so nothing forbidden was said."""
        rule = with_scope(text={"not_contains": "ESTORNO"})
        assert (
            evaluate(money(description=None), [an_invoice()], one_rule(rule)).port
            == "linked"
        )


# ===========================================================================
# Conditions working together
# ===========================================================================
class TestCombinations:
    """Every condition must hold: there is no ANY mode, on purpose.

    The categorization rules above this feature offer AND/OR because a
    wrong guess there is a mislabelled row. Here a wrong guess binds money
    to a debt, and "the amount matches OR the date is close" has no safe
    reading.
    """

    def test_all_conditions_must_hold(self):
        rule = with_scope(
            accounts={"in": [str(ACCOUNT)]},
            text={"contains": "PIX"},
            amount={"match": "exact", "max": "5000"},
            currency={"conversion": "reject", "in": ["BRL"]},
        )
        assert evaluate(money(), [an_invoice()], one_rule(rule)).port == "linked"

    @pytest.mark.parametrize(
        "broken",
        [
            {"accounts": {"in": [str(OTHER_ACCOUNT)]}},
            {"text": {"contains": "BOLETO"}},
            {"amount": {"match": "exact", "max": "100"}},
            {"currency": {"conversion": "reject", "in": ["USD"]}},
            {"direction": "debit"},
            {"payees": {"in": [str(OTHER_CLIENT)]}},
        ],
    )
    def test_one_failing_condition_is_enough_to_stop_the_rule(self, broken):
        """Parametrised because the failure has to be symmetric: a rule
        with five conditions that fires when four hold is not a rule."""
        rule = with_scope(
            **{
                "accounts": {"in": [str(ACCOUNT)]},
                "text": {"contains": "PIX"},
                "amount": {"match": "exact", "max": "5000"},
                "currency": {"conversion": "reject", "in": ["BRL"]},
                "direction": "credit",
                "payees": {"in": [str(CLIENT)]},
                **broken,
            }
        )
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert decision.port == "unmatched"

    def test_a_narrow_rule_can_run_before_a_broad_one(self):
        """The whole reason order is editable: a specific client's
        exception has to be consulted before the general rule that would
        otherwise swallow it."""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "this_client_needs_a_human",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": with_scope(payees={"in": [str(CLIENT)]}),
                },
                {
                    "id": "everyone_else_links",
                    "enabled": True,
                    "outcome": "link",
                    "when": BASE,
                },
            ],
            "on_ambiguity": "suggest",
        }
        theirs = evaluate(money(), [an_invoice()], policy)
        assert theirs.port == "suggested"
        assert theirs.strategy == "this_client_needs_a_human"

        anyone = evaluate(
            money(payee_id=OTHER_CLIENT), [an_invoice(payee_id=OTHER_CLIENT)], policy
        )
        assert anyone.port == "linked"
        assert anyone.strategy == "everyone_else_links"

    def test_a_rule_out_of_scope_lets_the_next_one_try(self):
        """Out of scope is not a verdict on the money: it means this rule
        had nothing to say, and the rest still get their turn."""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "dollars_only",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": with_scope(currency={"conversion": "reject", "in": ["USD"]}),
                },
                {"id": "anything", "enabled": True, "outcome": "link", "when": BASE},
            ],
            "on_ambiguity": "suggest",
        }
        decision = evaluate(money(), [an_invoice()], policy)
        assert decision.port == "linked"
        assert decision.strategy == "anything"

    def test_the_trace_says_which_rules_never_applied(self):
        """"No rule matched" is useless. "That rule is for dollars and
        this was reais" is something a person can act on."""
        rule = with_scope(currency={"conversion": "reject", "in": ["USD"]})
        decision = evaluate(money(), [an_invoice()], one_rule(rule))
        assert [n.rejected_by for n in decision.trace] == [Reason.OUT_OF_SCOPE]

    def test_scope_is_judged_once_however_many_promises_are_open(self):
        """The rule was never consulted, so there is nothing to say about
        any particular invoice: one note, not one per candidate."""
        rule = with_scope(accounts={"in": [str(OTHER_ACCOUNT)]})
        decision = evaluate(
            money(), [an_invoice(), an_invoice(), an_invoice()], one_rule(rule)
        )
        assert len(decision.trace) == 1


# ===========================================================================
# The use cases behind the conditions
# ===========================================================================
class TestRealWorldCases:
    """Whole rules as somebody would actually write them.

    Each of these is a sentence a person said, turned into a document.
    They exist because a condition that works in isolation and cannot
    express the request it was built for has not been delivered.
    """

    def test_large_receipts_get_a_human_and_small_ones_settle_themselves(self):
        """*"Anything over ten thousand I want to look at myself."*"""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "large_needs_review",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": with_scope(amount={"match": "exact", "min": "10000"}),
                },
                {
                    "id": "the_rest_link",
                    "enabled": True,
                    "outcome": "link",
                    "when": BASE,
                },
            ],
            "on_ambiguity": "suggest",
        }
        big = evaluate(
            money(amount=Decimal("25000.00")),
            [an_invoice(amount=Decimal("25000.00"))],
            policy,
        )
        assert big.port == "suggested"

        small = evaluate(money(), [an_invoice()], policy)
        assert small.port == "linked"

    def test_foreign_currency_receipts_are_never_linked_automatically(self):
        """*"Dollars I always want to check, because of the rate."*"""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "foreign_needs_review",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": with_scope(
                        currency={"conversion": "allow", "foreign": True}
                    ),
                },
                {"id": "home_links", "enabled": True, "outcome": "link", "when": BASE},
            ],
            "on_ambiguity": "suggest",
        }
        dollars = evaluate(
            money(currency="USD"),
            [an_invoice(currency="USD")],
            policy,
            base_currency="BRL",
        )
        assert dollars.port == "suggested"

        reais = evaluate(money(), [an_invoice()], policy, base_currency="BRL")
        assert reais.port == "linked"

    def test_one_difficult_client_is_always_reviewed(self):
        """*"This customer pays in parts and never the same way twice."*"""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "that_client",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": with_scope(
                        payees={"in": [str(OTHER_CLIENT)]},
                        amount={"match": "tolerance", "percent": "50"},
                    ),
                },
                {"id": "everyone_else", "enabled": True, "outcome": "link", "when": BASE},
            ],
            "on_ambiguity": "suggest",
        }
        decision = evaluate(
            money(amount=Decimal("2000.00"), payee_id=OTHER_CLIENT),
            [an_invoice(payee_id=OTHER_CLIENT)],
            policy,
        )
        assert decision.port == "suggested"
        assert decision.strategy == "that_client"

    def test_a_payments_account_is_automated_and_the_operating_one_is_not(self):
        """*"The account the gateway pays into can settle itself. The one
        I move money around in should not."*"""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "gateway_account",
                    "enabled": True,
                    "outcome": "link",
                    "when": with_scope(accounts={"in": [str(ACCOUNT)]}),
                }
            ],
            "on_ambiguity": "suggest",
        }
        assert evaluate(money(), [an_invoice()], policy).port == "linked"
        assert (
            evaluate(money(account_id=OTHER_ACCOUNT), [an_invoice()], policy).port
            == "unmatched"
        )

    def test_a_reversal_is_never_taken_as_a_payment(self):
        """*"An estorno is money coming back, not a client paying."* The
        amount and the client are both right, which is exactly why the
        text condition has to be able to override them."""
        rule = with_scope(text={"not_contains": "ESTORNO"})
        decision = evaluate(
            money(description="TED ESTORNO PARCIAL"), [an_invoice()], one_rule(rule)
        )
        assert decision.port == "unmatched"

    def test_a_named_transfer_from_one_account_within_a_ceiling(self):
        """Four conditions at once, which is what a real rule looks like
        once somebody has been burned twice."""
        rule = with_scope(
            accounts={"in": [str(ACCOUNT)]},
            text={"contains": "TED"},
            amount={"match": "exact", "max": "50000"},
            currency={"conversion": "reject", "in": ["BRL"]},
        )
        decision = evaluate(
            money(description="TED RECEBIDA CLIENTE ALPHA"),
            [an_invoice()],
            one_rule(rule),
        )
        assert decision.port == "linked"


# ===========================================================================
# The shipped rules still behave, with the new vocabulary present
# ===========================================================================
class TestShippedRulesUnchanged:
    """The conditions above are additions. Nothing that shipped may have
    changed meaning because the engine learned new words."""

    def test_the_invoice_rules_carry_no_scope_filters(self):
        """A shipped rule that quietly limited itself to one account would
        be a default nobody could explain."""
        policy = policy_module.default_policy("reconciliation.match_invoice")
        for strategy in policy["strategies"]:
            when = strategy["when"]
            assert "accounts" not in when
            assert "payees" not in when
            assert "text" not in when
            assert "direction" not in when
            assert "min" not in when.get("amount", {})
            assert "max" not in when.get("amount", {})

    def test_a_known_client_paying_exactly_still_links(self):
        policy = policy_module.default_policy("reconciliation.match_invoice")
        assert evaluate(money(), [an_invoice()], policy).port == "linked"

    def test_the_recurring_rule_still_links_on_the_same_signals(self):
        policy = policy_module.for_recurring("monthly")
        charge = Movement(
            amount=Decimal("89.90"),
            currency="BRL",
            direction="debit",
            when=TODAY + timedelta(days=2),
            description="NETFLIX ASSINATURA",
            account_id=ACCOUNT,
            source="sync",
        )
        occurrence = Expectation(
            kind="recurring",
            id=uuid.uuid4(),
            amount=Decimal("89.90"),
            currency="BRL",
            direction="debit",
            when=TODAY,
            description="NETFLIX ASSINATURA",
            account_id=ACCOUNT,
        )
        assert evaluate(charge, [occurrence], policy).port == "linked"


# ===========================================================================
# Two transactions, one invoice
# ===========================================================================
class TestPartPayments:
    """One invoice settled by several payments.

    The ledger has always allowed it: allocations are many-to-one with an
    amount, but until this mode existed nothing could ever *propose* the
    first half. Every other comparison measures against the whole
    outstanding balance, and half of it is simply not that. So a client
    paying R$3.000 in two transfers produced two unmatched rows and an
    invoice that looked untouched.
    """

    def part_rule(self, outcome: str = "suggest", **extra) -> dict:
        return one_rule(
            {
                "counterparty": "same_payee",
                "amount": {"match": "partial", "min_ratio": "0.05", **extra},
                "date": {"before_days": 30, "after_days": 30},
            },
            outcome=outcome,
        )

    def test_half_of_what_is_owed_is_offered(self):
        decision = evaluate(
            money(amount=Decimal("1500.00")), [an_invoice()], self.part_rule()
        )
        assert decision.port == "suggested"
        assert decision.amount == Decimal("1500.00")

    def test_what_is_left_over_is_named(self):
        """So the screen can say "R$1.500 of R$3.000" rather than leaving a
        reader to do the subtraction."""
        decision = evaluate(
            money(amount=Decimal("1200.00")), [an_invoice()], self.part_rule()
        )
        assert decision.difference == Decimal("1800.00")
        assert decision.difference_kind == "part_payment"

    def test_the_whole_amount_is_not_a_part_payment(self):
        """Strictly less than, so this and an exact match never both fire
        and a trace never has to explain which one won."""
        decision = evaluate(money(), [an_invoice()], self.part_rule())
        assert decision.port == "unmatched"

    def test_more_than_is_owed_is_not_a_part_payment_either(self):
        decision = evaluate(
            money(amount=Decimal("5000.00")), [an_invoice()], self.part_rule()
        )
        assert decision.port == "unmatched"

    def test_a_token_amount_is_not_offered_as_an_instalment(self):
        """R$10 against a R$3.000 invoice is noise. Offering it would teach
        people to stop reading the queue, which costs more than the one
        match it might have caught."""
        decision = evaluate(
            money(amount=Decimal("10.00")), [an_invoice()], self.part_rule()
        )
        assert decision.port == "unmatched"

    def test_the_floor_is_a_fraction_so_it_scales(self):
        """Five per cent means the same thing on a small invoice and a
        large one, which an absolute floor could not."""
        small = evaluate(
            money(amount=Decimal("10.00")),
            [an_invoice(amount=Decimal("100.00"))],
            self.part_rule(),
        )
        assert small.port == "suggested", "ten per cent of a small invoice is real"

    def test_the_floor_can_be_removed(self):
        decision = evaluate(
            money(amount=Decimal("1.00")),
            [an_invoice()],
            self.part_rule(min_ratio="0"),
        )
        assert decision.port == "suggested"

    def test_a_workspace_may_promote_part_payments_to_linking(self):
        """For somebody whose clients always pay in parts, confirming each
        one is the manual work the feature exists to remove."""
        decision = evaluate(
            money(amount=Decimal("1500.00")), [an_invoice()], self.part_rule("link")
        )
        assert decision.port == "linked"

    def test_the_second_payment_matches_exactly_once_the_first_is_booked(self):
        """Which is why the shipped default only suggests: after the first
        half is recorded, the balance is 1500 and the second half is an
        ordinary exact match. The ambiguity only ever exists at the start."""
        remaining = an_invoice(amount=Decimal("1500.00"))
        decision = evaluate(
            money(amount=Decimal("1500.00")), [remaining], one_rule(BASE)
        )
        assert decision.port == "linked"
        assert decision.amount == Decimal("1500.00")

    def test_a_part_payment_never_claims_more_than_is_owed(self):
        decision = evaluate(
            money(amount=Decimal("2999.99")), [an_invoice()], self.part_rule()
        )
        assert decision.amount is not None
        assert decision.amount == Decimal("2999.99")
        assert decision.amount < Decimal("3000.00")


# ===========================================================================
# Which moment a rule runs at
# ===========================================================================
class TestTrigger:
    """Money arriving, or a document being written.

    Two different questions with different evidence behind them, and until
    now the answer was hardcoded in whichever function happened to be
    doing the looking: a restriction nobody could see or change, on a
    feature whose entire premise is that matching is not a black box.
    """

    def only_when(self, trigger: str) -> dict:
        policy = one_rule(BASE)
        policy["strategies"][0]["trigger"] = trigger
        return policy

    def test_a_rule_for_arriving_money_sits_out_the_look_back(self):
        decision = evaluate(
            money(), [an_invoice()], self.only_when("money_arrives"),
            trigger="invoice_issued",
        )
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.WRONG_MOMENT

    def test_a_rule_for_the_look_back_sits_out_arriving_money(self):
        decision = evaluate(
            money(), [an_invoice()], self.only_when("invoice_issued"),
            trigger="money_arrives",
        )
        assert decision.port == "unmatched"
        assert decision.trace[0].rejected_by is Reason.WRONG_MOMENT

    def test_both_runs_at_either_moment(self):
        policy = self.only_when("both")
        for moment in ("money_arrives", "invoice_issued"):
            assert evaluate(money(), [an_invoice()], policy, trigger=moment).port == "linked"

    def test_a_rule_that_says_nothing_runs_when_money_arrives(self):
        """The common case, and the one a rule written without thinking
        about this should mean."""
        policy = one_rule(BASE)
        assert "trigger" not in policy["strategies"][0]
        assert evaluate(money(), [an_invoice()], policy).port == "linked"
        assert (
            evaluate(money(), [an_invoice()], policy, trigger="invoice_issued").port
            == "unmatched"
        )

    def test_the_wrong_moment_is_told_apart_from_a_failed_comparison(self):
        """"This rule runs at the other moment" and "this rule looked and
        said no" are different problems with different fixes, so they are
        different reasons."""
        decision = evaluate(
            money(), [an_invoice()], self.only_when("invoice_issued")
        )
        assert decision.trace[0].rejected_by is Reason.WRONG_MOMENT
        assert decision.trace[0].rejected_by is not Reason.OUT_OF_SCOPE

    def test_a_rule_at_the_wrong_moment_lets_the_next_one_try(self):
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "look_back_only",
                    "enabled": True,
                    "outcome": "suggest",
                    "trigger": "invoice_issued",
                    "when": BASE,
                },
                {
                    "id": "arrivals",
                    "enabled": True,
                    "outcome": "link",
                    "trigger": "money_arrives",
                    "when": BASE,
                },
            ],
            "on_ambiguity": "suggest",
        }
        decision = evaluate(money(), [an_invoice()], policy)
        assert decision.port == "linked"
        assert decision.strategy == "arrivals"


class TestShippedTriggers:
    """What the defaults say about the two moments, and why.

    These are product decisions rather than arithmetic, so they are pinned
    here: a change to any of them changes whose money gets claimed by a
    document written afterwards.
    """

    def shipped(self, rule_id: str) -> dict:
        policy = policy_module.default_policy("reconciliation.match_invoice")
        return next(s for s in policy["strategies"] if s["id"] == rule_id)

    def test_a_known_client_paying_exactly_is_trusted_at_both_moments(self):
        """The pay-then-invoice case is ordinary here, and a named client
        paying their exact amount is as convincing before the nota as
        after it."""
        assert self.shipped("same_client_exact")["trigger"] == "both"

    def test_an_unnamed_payer_is_only_trusted_when_a_promise_was_waiting(self):
        """Backwards, that money already had a life of its own: a refund,
        a transfer, another job, and claiming it for a document written
        afterwards is a guess."""
        assert self.shipped("exact_amount_any_client")["trigger"] == "money_arrives"

    def test_part_payments_are_offered_at_both_moments_and_never_linked(self):
        rule = self.shipped("same_client_part_payment")
        assert rule["trigger"] == "both"
        assert rule["outcome"] == "suggest"
        assert rule["when"]["amount"]["match"] == "partial"


# ===========================================================================
# One transaction, several invoices
# ===========================================================================
class TestSets:
    """A payment that answers several promises at once.

    The gateway payout, the client clearing three of their own invoices in
    one transfer, the commercial arrangement that settles a month at a
    time. The ledger has always been able to record it: allocations are
    many-to-many with an amount, but a decision that could only name one
    promise could never propose it.

    The hard part is not the arithmetic, it is **refusing when there is
    more than one answer**. Three invoices of a thousand and a credit of
    two thousand admit three equally good readings, and picking one would
    be inventing certainty exactly where the single-promise path refuses.
    """

    def set_rule(self, outcome: str = "link", **amount) -> dict:
        return one_rule(
            {
                "counterparty": "same_payee",
                "amount": {"match": "set", "max_invoices": 6, "percent": "0", **amount},
                "date": {"before_days": 30, "after_days": 30},
            },
            outcome=outcome,
        )

    def test_a_payment_covering_two_invoices_settles_both(self):
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [an_invoice(amount=Decimal("1000.00")), an_invoice(amount=Decimal("2000.00"))],
            self.set_rule(),
        )
        assert decision.port == "linked"
        assert len(decision.settlements) == 2
        assert sum(s.amount for s in decision.settlements) == Decimal("3000.00")

    def test_a_payment_covering_three_invoices_settles_all_three(self):
        decision = evaluate(
            money(amount=Decimal("6000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00")),
                an_invoice(amount=Decimal("3000.00")),
            ],
            self.set_rule(),
        )
        assert decision.port == "linked"
        assert len(decision.settlements) == 3

    def test_each_invoice_is_settled_for_its_own_amount(self):
        """Not the payment split evenly. The whole point of allocations
        carrying an amount is that each debt gets what it is owed."""
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [an_invoice(amount=Decimal("1000.00")), an_invoice(amount=Decimal("2000.00"))],
            self.set_rule(),
        )
        assert sorted(s.amount for s in decision.settlements) == [
            Decimal("1000.00"),
            Decimal("2000.00"),
        ]

    def test_two_combinations_that_both_add_up_settle_nothing_automatically(self):
        """Three invoices of a thousand and a payment of two thousand: any
        two of them fit. Which two is a question about intent, not
        arithmetic."""
        decision = evaluate(
            money(amount=Decimal("2000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("1000.00")),
            ],
            self.set_rule(),
        )
        assert decision.port == "suggested"
        assert any(n.rejected_by is Reason.AMBIGUOUS_SET for n in decision.trace)

    def test_an_ambiguous_set_still_offers_one_reading_to_start_from(self):
        """A person answering the question needs something concrete in
        front of them, not an empty prompt."""
        decision = evaluate(
            money(amount=Decimal("2000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("1000.00")),
            ],
            self.set_rule(),
        )
        assert len(decision.settlements) == 2

    def test_a_single_invoice_is_left_to_the_ordinary_rules(self):
        """A set rule is about combinations. One invoice matching exactly
        is what the exact rule is for, and having both claim it would make
        the trace ambiguous about which fired."""
        decision = evaluate(
            money(), [an_invoice()], self.set_rule()
        )
        assert decision.port == "unmatched"

    def test_smaller_combinations_are_preferred_over_larger_ones(self):
        """A payment that is exactly one pair should not be reported as
        also being a trio that happens to sum the same."""
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00")),
                an_invoice(amount=Decimal("500.00")),
                an_invoice(amount=Decimal("2500.00")),
            ],
            self.set_rule(),
        )
        # Two pairs add up, so it is ambiguous, and the reading offered is
        # a pair rather than something longer.
        assert decision.port == "suggested"
        assert len(decision.settlements) == 2

    def test_a_payment_that_matches_nothing_together_is_left_alone(self):
        decision = evaluate(
            money(amount=Decimal("7777.00")),
            [an_invoice(amount=Decimal("1000.00")), an_invoice(amount=Decimal("2000.00"))],
            self.set_rule(),
        )
        assert decision.port == "unmatched"

    def test_only_this_clients_invoices_are_combined(self):
        """The counterparty is what keeps the search small and the answer
        meaningful: adding up strangers' invoices to reach a total is
        numerology, not reconciliation."""
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00"), payee_id=OTHER_CLIENT),
            ],
            self.set_rule(),
        )
        assert decision.port == "unmatched"

    def test_invoices_outside_the_date_window_are_not_combined(self):
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00"), when=TODAY - timedelta(days=200)),
            ],
            self.set_rule(),
        )
        assert decision.port == "unmatched"

    def test_invoices_in_another_currency_are_not_combined(self):
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00"), currency="USD"),
            ],
            self.set_rule(),
        )
        assert decision.port == "unmatched"

    def test_a_fee_can_be_allowed_for_and_is_named(self):
        """The gateway case: the payout is the invoices minus the cut. The
        gap is reported rather than swallowed, so the caller can book it
        instead of leaving money unexplained."""
        decision = evaluate(
            money(amount=Decimal("2940.00")),
            [an_invoice(amount=Decimal("1000.00")), an_invoice(amount=Decimal("2000.00"))],
            self.set_rule(percent="2"),
        )
        assert decision.port == "linked"
        assert decision.difference == Decimal("-60.00")
        assert decision.difference_kind == "set_difference"

    def test_without_a_tolerance_a_fee_stops_the_match(self):
        """Shipped at zero on purpose. Guessing which fee applies is how a
        wrong split gets written confidently."""
        decision = evaluate(
            money(amount=Decimal("2940.00")),
            [an_invoice(amount=Decimal("1000.00")), an_invoice(amount=Decimal("2000.00"))],
            self.set_rule(),
        )
        assert decision.port == "unmatched"

    def test_the_number_of_invoices_in_one_answer_is_capped(self):
        """A cap the rule sets, bounded again by the engine. Somebody who
        allows two cannot be handed a combination of four."""
        decision = evaluate(
            money(amount=Decimal("4000.00")),
            [an_invoice(amount=Decimal("1000.00")) for _ in range(4)],
            self.set_rule(max_invoices=2),
        )
        assert decision.port == "unmatched"

    def test_too_many_open_invoices_is_refused_out_loud(self):
        """Searching which of forty invoices add up to a payment is
        subset-sum, and a sync that hangs is worse than a match that is
        not made. Refusing silently would leave somebody wondering why
        their payout never matched."""
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [an_invoice(amount=Decimal("100.00")) for _ in range(40)],
            self.set_rule(),
        )
        assert decision.port == "unmatched"
        assert any(n.rejected_by is Reason.TOO_MANY_TO_COMBINE for n in decision.trace)

    def test_a_settled_invoice_is_never_part_of_a_combination(self):
        decision = evaluate(
            money(amount=Decimal("3000.00")),
            [
                an_invoice(amount=Decimal("1000.00")),
                an_invoice(amount=Decimal("2000.00")),
                an_invoice(amount=Decimal("0")),
            ],
            self.set_rule(),
        )
        assert decision.port == "linked"
        assert all(s.expectation.amount > Decimal("0") for s in decision.settlements)

    def test_a_set_rule_that_finds_nothing_lets_the_next_rule_try(self):
        """It is one strategy among several, not a terminal branch."""
        policy = {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": [
                {
                    "id": "sets",
                    "enabled": True,
                    "outcome": "link",
                    "when": {
                        "counterparty": "same_payee",
                        "amount": {"match": "set"},
                        "date": {"before_days": 30, "after_days": 30},
                    },
                },
                {"id": "singles", "enabled": True, "outcome": "link", "when": BASE},
            ],
            "on_ambiguity": "suggest",
        }
        decision = evaluate(money(), [an_invoice()], policy)
        assert decision.port == "linked"
        assert decision.strategy == "singles"


class TestSettlementsAreAlwaysPresent:
    """Every decision names its settlements, even the ordinary one.

    A caller that has to ask "is this the single kind or the several kind"
    before it can write anything is a caller that will one day forget to.
    """

    def test_an_ordinary_match_carries_one_settlement(self):
        decision = evaluate(money(), [an_invoice()], one_rule(BASE))
        assert len(decision.settlements) == 1
        assert decision.settlements[0].expectation is decision.expectation
        assert decision.settlements[0].amount == decision.amount

    def test_an_unmatched_decision_carries_none(self):
        decision = evaluate(
            money(currency="USD"), [an_invoice()], one_rule(BASE)
        )
        assert decision.settlements == []


# ===========================================================================
# Bands: link under 2%, ask between 2% and 5%, refuse above
# ===========================================================================
class TestTolerangeBands:
    """*"Under two per cent I want it linked. Between two and five, ask me.
    Above five, leave it alone."*

    The shape the ordered list was built for: first match wins, so a band
    is just a looser rule placed after a tighter one. Nothing new is
    needed, but the rules shipped *below* a person's own still get their
    turn, and that is where this gets interesting.
    """

    def banded(self, *, with_shipped_partial: bool = False) -> dict:
        strategies = [
            {
                "id": "under_two",
                "enabled": True,
                "outcome": "link",
                "when": {
                    "counterparty": "same_payee",
                    "amount": {"match": "tolerance", "percent": "2"},
                    "date": {"before_days": 30, "after_days": 30},
                },
            },
            {
                "id": "two_to_five",
                "enabled": True,
                "outcome": "suggest",
                "when": {
                    "counterparty": "same_payee",
                    "amount": {"match": "tolerance", "percent": "5"},
                    "date": {"before_days": 30, "after_days": 30},
                },
            },
        ]
        if with_shipped_partial:
            # What actually sits below a workspace's own rules.
            strategies.append(
                {
                    "id": "same_client_part_payment",
                    "enabled": True,
                    "outcome": "suggest",
                    "when": {
                        "counterparty": "same_payee",
                        "amount": {
                            "match": "partial",
                            "min_ratio": "0.05",
                            "max_ratio": "0.95",
                        },
                        "date": {"before_days": 30, "after_days": 30},
                    },
                }
            )
        return {
            "version": 1,
            "node": "test",
            "scope": {"movement": "any", "ignore_transaction_sources": []},
            "strategies": strategies,
            "on_ambiguity": "suggest",
        }

    def test_a_one_percent_difference_links(self):
        decision = evaluate(
            money(amount=Decimal("2970.00")), [an_invoice()], self.banded()
        )
        assert decision.port == "linked"
        assert decision.strategy == "under_two"

    def test_a_three_percent_difference_is_asked_about(self):
        """The band is expressed by *order*, not by a lower bound: the 5%
        rule only ever sees what the 2% rule did not take."""
        decision = evaluate(
            money(amount=Decimal("2910.00")), [an_invoice()], self.banded()
        )
        assert decision.port == "suggested"
        assert decision.strategy == "two_to_five"

    def test_the_bands_hold_on_both_sides_of_the_amount(self):
        """Tolerance is symmetric, so an overpayment falls in the same
        band as an underpayment of the same size."""
        over = evaluate(
            money(amount=Decimal("3090.00")), [an_invoice()], self.banded()
        )
        assert over.port == "suggested" and over.strategy == "two_to_five"

    def test_the_boundary_belongs_to_the_tighter_rule(self):
        """Exactly two per cent links rather than asks: inclusive, and on
        the side a person means when they write "under 2%"."""
        decision = evaluate(
            money(amount=Decimal("2940.00")), [an_invoice()], self.banded()
        )
        assert decision.port == "linked"

    def test_above_the_last_band_nothing_matches(self):
        """"Reject" is not a verb the engine has. It is what happens when
        no rule claims the money: same outcome, reached by absence."""
        decision = evaluate(
            money(amount=Decimal("2700.00")), [an_invoice()], self.banded()
        )
        assert decision.port == "unmatched"

    def test_but_a_shipped_rule_below_still_gets_its_turn(self):
        """**The non-obvious part.** A 10% difference is not rejected while
        the shipped part-payment rule sits underneath: to that rule, 90% of
        a balance is an instalment, and it offers it.

        Which is correct in isolation and wrong for somebody who said
        "above five per cent, leave it alone", so expressing a real
        ceiling means narrowing or turning off what lies below it, not
        only adding rules above.
        """
        decision = evaluate(
            money(amount=Decimal("2700.00")),
            [an_invoice()],
            self.banded(with_shipped_partial=True),
        )
        assert decision.port == "suggested"
        assert decision.strategy == "same_client_part_payment"

    def test_a_real_ceiling_means_turning_off_what_lies_underneath(self):
        """The two rules tile the space exactly, which is why narrowing is
        not enough.

        A 5% tolerance reaches down to 95% of the balance. The shipped
        part-payment rule offers from 5% *to* 95% of it. They meet at the
        same point with no gap, so the region a person means by "above
        five per cent" is precisely the region part-payment claims.

        There is no threshold to tune: the resolution is the switch that
        is already on the page. Worth knowing before somebody spends an
        afternoon adjusting numbers that cannot express what they want.
        """
        with_partial = self.banded(with_shipped_partial=True)
        under_five = evaluate(
            money(amount=Decimal("2700.00")), [an_invoice()], with_partial
        )
        assert under_five.port == "suggested"
        assert under_five.strategy == "same_client_part_payment"

        with_partial["strategies"][2]["enabled"] = False
        assert (
            evaluate(money(amount=Decimal("2700.00")), [an_invoice()], with_partial).port
            == "unmatched"
        )

    def test_the_two_rules_meet_exactly_and_leave_no_gap(self):
        """Pinned because it is arithmetic somebody will otherwise
        rediscover by being confused: at exactly 95% of the balance both
        rules would fire, and the tighter one placed first takes it."""
        at_the_seam = money(amount=Decimal("2850.00"))
        decision = evaluate(
            at_the_seam, [an_invoice()], self.banded(with_shipped_partial=True)
        )
        assert decision.port == "suggested"
        assert decision.strategy == "two_to_five", "the band above wins the seam"


class TestMoneyInAnotherCurrency:
    """Never matched, and not a setting somebody can turn off.

    There used to be a `currency.conversion` knob whose `allow` value read
    as *convert and compare*. It did no such thing: the engine is pure and
    cannot look up a rate, so all `allow` did was stop comparing
    currencies. A $3.000 payment became an **exact** match for a €3.000
    invoice, and settled the euros with the dollars.
    """

    def test_a_payment_in_another_currency_never_settles_an_invoice(self):
        decision = evaluate(
            money(currency="USD"),
            [an_invoice(currency="EUR")],
            one_rule(with_scope()),
            base_currency="USD",
        )

        assert decision.port == "unmatched"
        assert any(n.rejected_by is Reason.CURRENCY for n in decision.trace)

    def test_the_same_numbers_are_not_the_same_money(self):
        """The exact-amount rule is the dangerous one: both sides read
        3000, and only the currency says they are different money."""
        decision = evaluate(
            money(amount=Decimal("3000.00"), currency="USD"),
            [an_invoice(amount=Decimal("3000.00"), currency="EUR")],
            one_rule(with_scope()),
            base_currency="USD",
        )

        assert decision.settlements == []

    def test_asking_for_conversion_no_longer_turns_the_check_off(self):
        """The knob is gone from the engine, so a stored rule that still
        carries it is matched as if it never said anything."""
        decision = evaluate(
            money(currency="USD"),
            [an_invoice(currency="EUR")],
            one_rule(with_scope(currency={"conversion": "allow"})),
            base_currency="USD",
        )

        assert decision.port == "unmatched"

    def test_a_rule_cannot_ask_for_conversion_that_does_not_exist(self):
        """Refused on the way in rather than quietly ignored, so a policy
        file carrying it is reported instead of doing something other than
        what it says."""
        with pytest.raises(rule_service.RuleError) as caught:
            rule_service.validate_config(
                {"when": {"currency": {"conversion": "allow"}}}, whole=False
            )

        assert caught.value.code == "conversion_unsupported"
