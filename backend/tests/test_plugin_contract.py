"""The data a provider plugin returns from `sync` (contract 1)."""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.plugins import contract
from app.plugins.contract import PayloadError, parse_payload
from app.providers.base import ReservedBalanceData

ALL = ["accounts", "transactions", "bills", "holdings", "trades"]

PAYLOAD: dict[str, Any] = {
    "contract": 1,
    "accounts": [
        {
            "external_id": "A1:USD",
            "name": "Broker Cash (USD)",
            "type": "investment",
            "balance": "1234.56",
            "currency": "USD",
            "transactions": [
                {"external_id": "t1", "description": "Dividend", "amount": "12.34", "date": "2026-11-14",
                 "type": "credit", "provider_category": "Dividends"},
                {"external_id": "t2", "description": "Fee", "amount": "1", "date": "2026-11-15",
                 "type": "debit", "status": "pending"},
            ],
            "bills": None,
        }
    ],
    "holdings": [
        {
            "external_id": "A1:STK:1", "name": "Example ETF", "currency": "USD", "current_value": "1000.00",
            "quantity": "10", "ticker": "EXMP", "isin": "US0000000001",
            "trades": [
                {"external_id": "trade:1", "kind": "buy", "date": "2026-11-10", "quantity": "10",
                 "price": "98.5", "fee": "1"},
            ],
        }
    ],
}


def _payload(change=None) -> dict[str, Any]:
    data = copy.deepcopy(PAYLOAD)
    if change:
        change(data)
    return data


def test_a_payload_becomes_the_provider_dataclasses():
    payload = parse_payload(_payload(), ALL)
    assert payload is not None and payload.accounts and payload.holdings
    account = payload.accounts[0]
    assert account.transactions and payload.holdings[0].trades
    data = account.to_data()
    assert (data.external_id, data.type, data.balance, data.currency) == ("A1:USD", "investment", Decimal("1234.56"), "USD")
    first, second = (t.to_data() for t in account.transactions)
    assert first.pluggy_category == "Dividends" and first.status == "posted" and first.date == date(2026, 11, 14)
    assert second.status == "pending" and second.amount == Decimal("1")
    holding = payload.holdings[0].to_data()
    assert holding.quantity == Decimal("10") and holding.ticker == "EXMP" and holding.asset_type is None
    assert payload.holdings[0].trades[0].price == Decimal("98.5")
    assert payload.kinds() == {"accounts", "transactions", "holdings", "trades"}


def test_reserved_balances_become_provider_data():
    balances = [
        {"external_id": "emergency-fund", "name": "Emergency fund", "currency": "BRL", "amount": "2000"},
        {"external_id": "emergency-fund", "name": None, "currency": "USD", "amount": "0"},
    ]
    payload = parse_payload(_payload(lambda d: d["accounts"][0].update(reserved_balances=balances)), ALL)
    assert payload is not None and payload.accounts
    data = payload.accounts[0].to_data()
    assert data.reserved_balances == [
        ReservedBalanceData(
            external_id="emergency-fund", name="Emergency fund", currency="BRL", amount=Decimal("2000")
        ),
        ReservedBalanceData(external_id="emergency-fund", name=None, currency="USD", amount=Decimal("0")),
    ]


def test_reserved_balances_unreported_and_empty_are_different():
    omitted = parse_payload(_payload(), ALL)
    assert omitted is not None and omitted.accounts
    assert omitted.accounts[0].to_data().reserved_balances is None

    empty = parse_payload(_payload(lambda d: d["accounts"][0].update(reserved_balances=[])), ALL)
    assert empty is not None and empty.accounts
    assert empty.accounts[0].to_data().reserved_balances == []


def test_a_bill_keeps_its_raw_data_but_not_what_only_a_native_provider_may_say():
    bill = {"external_id": "b1", "due_date": "2026-12-10", "total_amount": "150", "currency": "USD",
            "raw_data": {"cycle": "2026-11", "financeCharges": [{"id": "c1", "amount": 9e99}]}}
    payload = parse_payload(_payload(lambda d: d["accounts"][0].update(bills=[bill])), ALL)
    assert payload is not None and payload.accounts and payload.accounts[0].bills
    # The pipeline writes transactions from a native bill's finance charges;
    # a plugin's transactions come from `transactions` only.
    assert payload.accounts[0].bills[0].to_data().raw_data == {"cycle": "2026-11"}


def test_no_data_means_nothing_was_fetched():
    assert parse_payload(None, ALL) is None


def test_null_lists_are_not_fetched_and_empty_lists_are():
    payload = parse_payload({"contract": 1, "accounts": [], "holdings": None}, ALL)
    assert payload is not None and payload.accounts == [] and payload.holdings is None
    assert payload.kinds() == {"accounts"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(contract=2), "contract 2"),
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d["accounts"][0].update(type="brokerage"), "type"),
        (lambda d: d["accounts"][0].update(balance=1.1), "floats"),
        (lambda d: d["accounts"][0].update(currency="usd"), "currency"),
        (lambda d: d["accounts"][0].update(masked_number="12345678"), "masked_number"),
        (lambda d: d["accounts"][0]["transactions"][0].update(amount="-1"), "amount"),
        (lambda d: d["accounts"][0]["transactions"][0].update(type="transfer"), "type"),
        (lambda d: d["accounts"][0]["transactions"][1].update(external_id="t1"), "unique"),
        (lambda d: d["accounts"].append(copy.deepcopy(d["accounts"][0])), "unique"),
        (lambda d: d["accounts"][0].update(reserved_balances=[
            {"external_id": "pocket", "currency": "BRL", "amount": "1"},
            {"external_id": "pocket", "currency": "BRL", "amount": "2"},
        ]), "unique"),
        (lambda d: d["accounts"][0].update(reserved_balances=[
            {"external_id": "pocket", "currency": "BRL", "amount": 1.5},
        ]), "floats"),
        (lambda d: d["accounts"][0].update(reserved_balances=[
            {"external_id": "pocket", "currency": "BRL", "amount": "-1"},
        ]), "amount"),
        (lambda d: d["accounts"][0].update(reserved_balances=[
            {"external_id": "pocket", "currency": "brl", "amount": "1"},
        ]), "currency"),
        (lambda d: d["accounts"][0].update(reserved_balances=[
            {"external_id": "pocket", "currency": "BRL", "balance": "1"},
        ]), "not permitted"),
        (lambda d: d["holdings"][0]["trades"][0].update(quantity="0"), "quantity"),
        (lambda d: d["holdings"][0]["trades"][0].update(kind="short"), "kind"),
        (lambda d: d["holdings"][0]["trades"].append(dict(d["holdings"][0]["trades"][0])), "unique"),
        (lambda d: d["accounts"][0].update(institution_logo_url="http://example.com/x.png"), "institution_logo_url"),
        (lambda d: d["holdings"][0].update(type="real_estate"), "type"),
        (lambda d: d["holdings"][0].update(quantity="1000000000"), "quantity"),
        (lambda d: d["accounts"][0].update(balance="10000000000000"), "balance"),
        (lambda d: d["holdings"][0].update(ticker="X" * 33), "ticker"),
        (lambda d: d["holdings"][0]["trades"][0].update(price="1000000000000"), "price"),
        # Fits as sent, but rounds up past the column once stored with six (or two) decimals.
        (lambda d: d["holdings"][0].update(quantity="999999999.9999999"), "quantity"),
        (lambda d: d["accounts"][0].update(balance="9999999999999.995"), "balance"),
        (lambda d: d["holdings"][0]["trades"][0].update(price="999999999999.9999995"), "price"),
    ],
    ids=["contract", "unknown-field", "account-type", "float", "currency", "full-number", "negative-amount",
         "transaction-type", "duplicate-transaction", "duplicate-account", "duplicate-reserved-balance",
         "reserved-amount-as-float", "negative-reserved-amount", "reserved-currency", "reserved-unknown-field",
         "zero-quantity", "trade-kind",
         "duplicate-trade", "insecure-logo", "holding-type", "units-too-large", "money-too-large",
         "ticker-too-long", "trade-price-too-large", "units-round-past-the-column",
         "money-rounds-past-the-column", "trade-price-rounds-past-the-column"],
)
def test_malformed_data_is_refused(change, message):
    with pytest.raises(PayloadError, match=message):
        parse_payload(_payload(change), ALL)


def test_errors_never_carry_the_values_sent():
    secret = "ACCOUNT-U1234567"
    with pytest.raises(PayloadError) as caught:
        parse_payload(_payload(lambda d: d["accounts"][0].update(type=secret)), ALL)
    assert secret not in str(caught.value)


def test_kinds_the_integration_does_not_provide_are_refused():
    with pytest.raises(PayloadError, match="holdings, trades"):
        parse_payload(_payload(), ["accounts", "transactions"])
    only_accounts = _payload(lambda d: (d.pop("holdings"), d["accounts"][0].update(transactions=None)))
    assert parse_payload(only_accounts, ["accounts"]) is not None


def test_replies_over_the_caps_are_refused(monkeypatch):
    monkeypatch.setattr(contract, "MAX_TRANSACTIONS", 1)
    with pytest.raises(PayloadError, match="2 transactions in one reply"):
        parse_payload(_payload(), ALL)


def test_the_reserved_balance_cap_counts_the_reply_and_says_it_cannot_be_split():
    """The cap is the reply's total, and its message must not send the author to
    a next sync: a reported list is the account's whole set, so what is left out
    of it is deleted."""

    def spread(data):
        first = data["accounts"][0]
        first["reserved_balances"] = [
            {"external_id": f"first-{i}", "currency": "BRL", "amount": "1.00"} for i in range(1_000)
        ]
        second = copy.deepcopy(first)
        second["external_id"] = "A2"
        second["reserved_balances"] = [
            {"external_id": f"second-{i}", "currency": "BRL", "amount": "1.00"} for i in range(1_001)
        ]
        data["accounts"].append(second)

    with pytest.raises(PayloadError, match="2001 reserved_balances in one reply") as caught:
        parse_payload(_payload(spread), ALL)
    message = str(caught.value)
    assert "the most is 2000, and a reply over that is refused whole" in message
    assert "whole set" in message and "can't be split across syncs" in message
    assert "next sync" not in message


def test_data_must_be_an_object():
    with pytest.raises(PayloadError, match="object"):
        parse_payload(["accounts"], ALL)
