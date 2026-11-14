"""Max and Isracard providers, with the israeli-banks sidecar faked by httpx.MockTransport.

All card numbers, ids and amounts here are made up.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers import KNOWN_PROVIDERS
from app.providers.base import ProviderUserActionRequired, SessionExpiredError
from app.providers.credential_crypto import seal, unseal
from app.providers.israeli_cards import IsracardProvider, MaxProvider

LOGIN = {"id": "000000018", "card6Digits": "123456", "password": "not-a-real-password"}

CARD = {
    "accountNumber": "4321",
    "currency": "ILS",
    "balance": -2000,
    "creditLimit": 10000,
    "txns": [
        {"date": "2026-03-02", "processedDate": "2026-04-02", "amount": -100, "currency": "ILS",
         "originalAmount": -100, "originalCurrency": "ILS", "description": "Supermarket",
         "memo": "", "identifier": "T1", "status": "posted", "installments": None},
        {"date": "2026-02-10", "processedDate": "2026-04-02", "amount": -50, "currency": "ILS",
         "originalAmount": -150, "originalCurrency": "ILS", "description": "Electronics store",
         "memo": "payment 2 of 3", "identifier": "T2", "status": "posted",
         "installments": {"number": 2, "total": 3}},
        {"date": "2026-03-05", "processedDate": "2026-04-02", "amount": -40, "currency": "ILS",
         "originalAmount": -10, "originalCurrency": "USD", "description": "Streaming service",
         "memo": "", "identifier": "T3", "status": "posted", "installments": None},
        # Billed in dollars: the charged amount is USD, not the card's ILS.
        {"date": "2026-03-07", "processedDate": "2026-04-02", "amount": -12.5, "currency": "USD",
         "originalAmount": -50, "originalCurrency": "PLN", "description": "Cafe abroad",
         "memo": "", "identifier": "T4", "status": "posted", "installments": None},
        {"date": "2026-03-09", "processedDate": "2026-04-02", "amount": 20, "currency": "ILS",
         "originalAmount": 20, "originalCurrency": "ILS", "description": "Refund",
         "memo": "", "identifier": "T5", "status": "posted", "installments": None},
        {"date": "2026-03-14", "processedDate": "2026-04-02", "amount": -5, "currency": "ILS",
         "originalAmount": -5, "originalCurrency": "ILS", "description": "Pending charge",
         "memo": "", "identifier": None, "status": "pending", "installments": None},
    ],
}


def _patched(provider_cls, handler):
    transport = httpx.MockTransport(handler)

    def fake_client(self):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, base_url="http://israeli-banks:8095", timeout=5)

    return patch.object(provider_cls, "_client", fake_client)


def _sidecar(seen: list | None = None, status: int = 200, error_type: str = "INVALID_PASSWORD"):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error_type": error_type, "error": "login failed"})
        return httpx.Response(200, json={"scraped_at": "2026-11-14T12:00:00Z", "accounts": [CARD]})

    return handler


def test_known_providers_list_their_login_fields():
    by_name = {p["name"]: p for p in KNOWN_PROVIDERS}
    assert by_name["max"]["flow_type"] == "credentials"
    assert by_name["max"]["credential_fields"] == ["username", "password"]
    assert by_name["isracard"]["credential_fields"] == ["id", "card6Digits", "password"]


def test_sealed_login_round_trips_and_is_not_plaintext():
    token = seal(LOGIN)
    assert LOGIN["password"] not in token
    assert unseal(token) == LOGIN
    assert unseal("garbage") is None
    assert unseal(None) is None


@pytest.mark.asyncio
async def test_connect_stores_the_login_encrypted():
    seen: list[dict] = []
    with _patched(IsracardProvider, _sidecar(seen)):
        conn = await IsracardProvider().handle_oauth_callback(json.dumps(LOGIN))
    assert seen[0]["company"] == "isracard"
    assert seen[0]["credentials"] == LOGIN
    assert set(conn.credentials) == {"sealed"}
    assert LOGIN["password"] not in json.dumps(conn.credentials)
    assert unseal(conn.credentials["sealed"]) == LOGIN
    assert LOGIN["id"] not in conn.external_id
    [card] = conn.accounts
    assert (card.type, card.name, card.masked_number) == ("credit_card", "Isracard ****4321", "4321")
    assert card.balance == Decimal("-2000")
    assert card.credit_limit == Decimal("10000")


@pytest.mark.asyncio
async def test_same_login_keeps_the_same_connection():
    with _patched(IsracardProvider, _sidecar()):
        first = await IsracardProvider().handle_oauth_callback(json.dumps(LOGIN))
        again = await IsracardProvider().handle_oauth_callback(json.dumps({**LOGIN, "password": "changed"}))
    assert first.external_id == again.external_id


@pytest.mark.asyncio
async def test_connect_rejects_missing_fields():
    with pytest.raises(ValueError, match="card6Digits"):
        await IsracardProvider().handle_oauth_callback(json.dumps({"id": "1", "password": "x"}))
    with pytest.raises(ValueError):
        await MaxProvider().handle_oauth_callback("not json")


@pytest.mark.asyncio
async def test_wrong_password_asks_the_user_to_reconnect():
    with _patched(MaxProvider, _sidecar(status=422, error_type="INVALID_PASSWORD")):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await MaxProvider().handle_oauth_callback(json.dumps({"username": "u", "password": "p"}))
    assert exc.value.code == "credentials_invalid"
    assert "p" not in str(exc.value).split()


@pytest.mark.asyncio
async def test_other_failures_are_errors_not_reconnects():
    with _patched(MaxProvider, _sidecar(status=422, error_type="TIMEOUT")):
        with pytest.raises(RuntimeError, match="Max read failed"):
            await MaxProvider().get_accounts({"sealed": seal({"username": "u", "password": "p"})})


@pytest.mark.asyncio
async def test_unreadable_stored_login_expires_the_connection():
    with pytest.raises(SessionExpiredError):
        await MaxProvider().get_accounts({"sealed": "rotated-secret-key"})


def test_transactions_cover_installments_foreign_and_refunds():
    txns = {t.description.split(" |")[0]: t for t in IsracardProvider.build_transactions(CARD)}
    assert "Pending charge" not in txns
    grocery = txns["Supermarket"]
    assert (grocery.amount, grocery.type, grocery.currency) == (Decimal("100"), "debit", "ILS")
    assert grocery.amount_in_account_currency is None
    installment = txns["Electronics store"]
    assert (installment.installment_number, installment.total_installments) == (2, 3)
    assert installment.amount == Decimal("50")
    assert installment.description == "Electronics store | payment 2 of 3"
    streaming = txns["Streaming service"]
    assert (streaming.currency, streaming.amount, streaming.amount_in_account_currency) == (
        "USD", Decimal("10"), Decimal("40"),
    )
    assert txns["Refund"].type == "credit"
    cafe = txns["Cafe abroad"]
    assert (cafe.currency, cafe.amount) == ("PLN", Decimal("50"))
    assert cafe.amount_in_account_currency is None  # 12.5 was dollars, not shekels


def test_transaction_ids_are_stable_and_unique():
    first = [t.external_id for t in IsracardProvider.build_transactions(CARD)]
    again = [t.external_id for t in IsracardProvider.build_transactions(CARD)]
    assert first == again and len(first) == len(set(first))


def _charge(**overrides):
    base = {"date": "2026-03-10", "processedDate": "2026-04-02", "amount": -30, "currency": "ILS",
            "originalAmount": -30, "originalCurrency": "ILS", "description": "Shop",
            "memo": "", "identifier": None, "status": "posted", "installments": None}
    return {**base, **overrides}


def test_placeholder_identifiers_are_not_treated_as_charge_ids():
    # Max builds the id from the deal's ARN; an installment without one comes
    # back as "undefined_1". Two unrelated purchases must not share an id.
    january = _charge(date="2026-01-10", description="Furniture", amount=-200, originalAmount=-200,
                      identifier="undefined_1", installments={"number": 1, "total": 3})
    february = _charge(date="2026-02-10", description="Laptop", amount=-500, originalAmount=-500,
                       identifier="undefined_1", installments={"number": 1, "total": 3})
    first = MaxProvider.build_transactions({"currency": "ILS", "txns": [january]})
    second = MaxProvider.build_transactions({"currency": "ILS", "txns": [february]})
    assert first[0].external_id != second[0].external_id
    assert (first[0].raw_data or {})["identifier"] is None


def test_unconverted_foreign_charge_leaves_the_bank_amount_unset():
    # Not converted yet: the shekel amount is 0, which is not a real conversion.
    txn = IsracardProvider.build_transactions({"currency": "ILS", "txns": [
        _charge(amount=0, originalAmount=-10, originalCurrency="USD", description="Online store"),
    ]})[0]
    assert (txn.currency, txn.amount, txn.type) == ("USD", Decimal("10"), "debit")
    assert txn.amount_in_account_currency is None


def test_since_filters_older_charges():
    txns = IsracardProvider.build_transactions(CARD, since=date(2026, 3, 1))
    assert all(t.date >= date(2026, 3, 1) for t in txns)


@pytest.mark.asyncio
async def test_transactions_read_through_the_stored_login():
    seen: list[dict] = []
    provider = IsracardProvider()
    sealed = {"sealed": seal(LOGIN)}
    with _patched(IsracardProvider, _sidecar(seen)):
        [card] = await provider.get_accounts(sealed)
        txns = await provider.get_transactions(sealed, card.external_id)
    assert len(txns) == 5
    assert all(body["credentials"] == LOGIN for body in seen)
