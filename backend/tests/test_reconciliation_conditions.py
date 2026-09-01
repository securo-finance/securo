"""Every condition a rule can carry, and what it does to a real match.

Pure tests, no database: the engine decides and never writes, which is
what lets this file ask several hundred questions in a fraction of a
second. Reconciliation binds money to debts, so the bar here is not "the
happy path works" — it is that each condition is exercised on **both**
sides of its boundary, and that combinations behave the way the sentence
on screen says they do.

Organised by the question each condition answers:

  - *Does this rule apply to money like this?* — account, payee list,
    direction, currency, amount band, statement text. These decide
    whether the rule is consulted at all.
  - *Does this money answer this promise?* — amount comparison, dates,
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

    QuickBooks makes this the first choice on every rule, and the reason
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
    is *the one on the invoice*. This names specific clients — the ask
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

    QuickBooks asks this before anything else. Here the expectation
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

    def test_scope_and_conversion_are_independent(self):
        """Naming a currency says which money the rule is for; conversion
        says whether the pair may disagree. A rule can do both."""
        rule = with_scope(currency={"conversion": "allow", "in": ["USD"]})
        decision = evaluate(
            money(currency="USD"), [an_invoice(currency="BRL")], one_rule(rule)
        )
        assert decision.port == "linked"


class TestAmountBand:
    """"Only under ten thousand", or "only above five hundred".

    Xero's amount operators, and the single most requested guardrail:
    small payments settle themselves, large ones get a human. It is a
    filter on the money, not a comparison with the promise — a rule can
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
        thousand — otherwise every band would need writing twice."""
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

    Bank text is where a payment's real identity hides, and Xero and
    QuickBooks both put it front and centre. It is also the bridge to
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
    """Every condition must hold — there is no ANY mode, on purpose.

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
        """Out of scope is not a verdict on the money — it means this rule
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
        any particular invoice — one note, not one per candidate."""
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
