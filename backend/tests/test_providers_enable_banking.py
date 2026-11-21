"""Unit tests for the Enable Banking provider.

Covers: JWT signing/claims, transaction fingerprint stability, account-type
mapping, nested vs flat transaction page shapes, restricted-mode handling.
HTTP is mocked end-to-end via httpx.MockTransport.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.providers.base import (
    ProviderUserActionRequired,
    SessionExpiredError,
    mask_last4,
)
from app.providers.enable_banking import (
    ACCOUNT_DETAILS_KEY,
    DEFAULT_HISTORY_DAYS,
    FALLBACK_HISTORY_DAYS,
    INITIAL_HISTORY_DAYS,
    INITIAL_TRANSACTION_PAGE_LIMIT,
    EnableBankingProvider,
    _account_identifier,
    _entry_reference_external_id,
    _map_cash_account_type,
    _repeat_external_id,
    _txn_fingerprint,
)
from app.providers.psu import PsuContext, psu_present


def _rsa_pem() -> tuple[str, str]:
    """Generate an in-memory RSA keypair and return PEM-encoded (private, public)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


@pytest.fixture
def eb_keys(monkeypatch):
    """Configure EB env vars with a fresh keypair and reset the provider cache."""
    private_pem, public_pem = _rsa_pem()
    monkeypatch.setenv("ENABLE_BANKING_APP_ID", "test-app-id-123")
    monkeypatch.setenv("ENABLE_BANKING_PRIVATE_KEY", private_pem)
    # Ensure the inline key wins over any file path the host env may have set.
    monkeypatch.setenv("ENABLE_BANKING_PRIVATE_KEY_FILE", "")
    # Force settings re-read.
    from app.core.config import get_settings

    get_settings.cache_clear()
    EnableBankingProvider._cached_token = None
    EnableBankingProvider._cached_token_exp = 0.0
    EnableBankingProvider._cached_private_key = None
    yield public_pem
    get_settings.cache_clear()


# ----- JWT signing -----


def test_jwt_token_has_expected_claims_and_kid(eb_keys):
    public_pem = eb_keys
    token = EnableBankingProvider._jwt_token()

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == "test-app-id-123"
    assert header["typ"] == "JWT"

    claims = jwt.decode(
        token, public_pem, algorithms=["RS256"], audience="api.enablebanking.com"
    )
    assert claims["iss"] == "enablebanking.com"
    assert claims["aud"] == "api.enablebanking.com"
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    # Expiry within the next hour.
    assert claims["exp"] - claims["iat"] <= 3600


def test_jwt_token_cached_across_calls(eb_keys):
    a = EnableBankingProvider._jwt_token()
    b = EnableBankingProvider._jwt_token()
    assert a == b


# ----- pure helpers -----


def test_cash_account_type_mapping():
    assert _map_cash_account_type("CACC") == "checking"
    assert _map_cash_account_type("SVGS") == "savings"
    assert _map_cash_account_type("CARD") == "credit_card"
    assert _map_cash_account_type("LOAN") == "checking"
    assert _map_cash_account_type(None) == "checking"
    assert _map_cash_account_type("UNKNOWN_TYPE") == "checking"


def test_txn_fingerprint_stable_for_same_payload():
    raw = {
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-05-20",
        "value_date": "2026-05-20",
        "remittance_information": ["Coffee shop"],
        "creditor_account": {"iban": "DE89370400440532013000"},
    }
    fp1 = _txn_fingerprint("acc-uid-1", raw)
    fp2 = _txn_fingerprint("acc-uid-1", dict(raw))
    assert fp1 == fp2
    assert len(fp1) == 32


def test_txn_fingerprint_differs_on_amount_change():
    base = {
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-05-20",
        "remittance_information": ["X"],
    }
    other = dict(base)
    other["transaction_amount"] = {"amount": "12.35", "currency": "EUR"}
    assert _txn_fingerprint("acc", base) != _txn_fingerprint("acc", other)


def test_txn_fingerprint_uses_counterparty_name_when_iban_is_missing():
    base = {
        "entry_reference": "0",
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-05-20",
        "remittance_information": [],
        "creditor": {"name": "Coffee Shop"},
    }
    other = {**base, "creditor": {"name": "Bakery"}}
    assert _txn_fingerprint("acc", base) != _txn_fingerprint("acc", other)


# ----- HTTP-driven parsing via httpx.MockTransport -----


def _mock_eb_transport(handler):
    return httpx.MockTransport(handler)


def _patch_client(provider: EnableBankingProvider, handler):
    """Replace _client() so requests hit our MockTransport."""
    transport = _mock_eb_transport(handler)

    def fake_client():
        return httpx.AsyncClient(
            base_url="https://api.enablebanking.com",
            transport=transport,
            headers={"Authorization": "Bearer test-jwt"},
        )

    return patch.object(provider, "_client", side_effect=fake_client)


@pytest.mark.asyncio
async def test_list_institutions_maps_and_dedupes_countries(eb_keys):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/aspsps"
        return httpx.Response(
            200,
            json={
                "aspsps": [
                    {
                        "name": "Revolut",
                        "country": "DE",
                        "logo": "https://l/revolut.png",
                        "bic": "REVOLT21",
                        "psu_types": ["personal"],
                        "maximum_consent_validity": 15552000,
                    },
                    {"name": "Sparkasse", "country": "DE"},
                    {"name": "BNP Paribas", "country": "FR"},
                ]
            },
        )

    with _patch_client(provider, handler):
        data = await provider.list_institutions()

    assert data.countries == ["DE", "FR"]
    names = [(i.country, i.name) for i in data.institutions]
    assert ("DE", "Revolut") in names
    assert ("FR", "BNP Paribas") in names
    revolut = next(i for i in data.institutions if i.name == "Revolut")
    assert revolut.max_consent_days == 180
    assert revolut.psu_types == ["personal"]


@pytest.mark.asyncio
async def test_get_oauth_url_returns_consent_url(eb_keys):
    provider = EnableBankingProvider()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth"
        body = request.read().decode()
        import json as _json

        captured.update(_json.loads(body))
        return httpx.Response(
            200,
            json={
                "url": "https://consent.enablebanking.com/abc",
                "authorization_id": "auth-1",
                "psu_id_hash": "hash-x",
            },
        )

    with _patch_client(provider, handler):
        url = await provider.get_oauth_url(
            "https://app.example.com/oauth/callback",
            "state-xyz",
            flow_params={"country": "de", "institution_name": "Revolut"},
        )
    assert url == "https://consent.enablebanking.com/abc"
    assert captured["aspsp"] == {"name": "Revolut", "country": "DE"}
    assert captured["redirect_url"] == "https://app.example.com/oauth/callback"
    assert captured["state"] == "state-xyz"
    assert captured["psu_type"] == "personal"
    assert "Z" in captured["access"]["valid_until"]


@pytest.mark.asyncio
async def test_get_oauth_url_keeps_consent_under_the_bank_max(eb_keys):
    """Trade Republic allows exactly 90 days of consent and EB rejects even a
    second more, so asking for the bank's max must land safely below it."""
    provider = EnableBankingProvider()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured.update(_json.loads(request.read().decode()))
        return httpx.Response(200, json={"url": "https://consent.enablebanking.com/abc"})

    with _patch_client(provider, handler):
        await provider.get_oauth_url(
            "https://app.example.com/oauth/callback",
            "state-xyz",
            flow_params={
                "country": "es",
                "institution_name": "Trade Republic",
                "valid_until_days": 90,
            },
        )
    valid_until = datetime.fromisoformat(captured["access"]["valid_until"])
    assert valid_until < datetime.now(timezone.utc) + timedelta(days=90) - timedelta(minutes=5)


@pytest.mark.asyncio
async def test_get_oauth_url_rejects_missing_flow_params(eb_keys):
    provider = EnableBankingProvider()
    with pytest.raises(ValueError):
        await provider.get_oauth_url(
            "https://x/cb", "s", flow_params={"country": "DE"}
        )


@pytest.mark.asyncio
async def test_handle_oauth_callback_restricted_mode_raises(eb_keys):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        return httpx.Response(
            200,
            json={
                "session_id": "sess-1",
                "accounts": None,
                "aspsp": {"name": "Revolut", "country": "DE"},
                "access": {"valid_until": "2026-12-01T00:00:00Z"},
            },
        )

    with _patch_client(provider, handler):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await provider.handle_oauth_callback("code-abc")
    assert exc.value.code == "no_accounts_linked"


@pytest.mark.asyncio
async def test_handle_oauth_callback_builds_connection_data(eb_keys):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "sess-2",
                    "accounts": [
                        {
                            "uid": "acc-uid-1",
                            "currency": "EUR",
                            "display_name": "Main",
                            "cash_account_type": "CACC",
                        }
                    ],
                    "aspsp": {"name": "Revolut", "country": "DE"},
                    "access": {"valid_until": "2026-12-01T00:00:00Z"},
                },
            )
        if path == "/accounts/acc-uid-1/balances":
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balance_type": "CLBD",
                            "balance_amount": {"amount": "123.45", "currency": "EUR"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patch_client(provider, handler):
        conn = await provider.handle_oauth_callback("code-abc")
    assert conn.external_id == "sess-2"
    assert conn.institution_name == "Revolut"
    assert len(conn.accounts) == 1
    acc = conn.accounts[0]
    assert acc.external_id == "acc-uid-1"
    assert acc.type == "checking"
    assert acc.balance == Decimal("123.45")
    assert acc.currency == "EUR"
    # session_id must NOT appear in credentials in plaintext.
    assert "session_id_enc" in conn.credentials
    assert conn.credentials.get("session_id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["-250000.00", "250000.00"])
async def test_loan_account_keeps_the_bank_reported_balance(eb_keys, reported):
    """Sign normalization happens on storage, by the account's stored type
    (account_service._provider_to_internal_balance), not in the provider."""
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "sess-loan",
                    "accounts": [
                        {
                            "uid": "loan-uid-1",
                            "currency": "EUR",
                            "display_name": "Mortgage",
                            "cash_account_type": "LOAN",
                        }
                    ],
                    "aspsp": {"name": "ING", "country": "NL"},
                    "access": {"valid_until": "2026-12-01T00:00:00Z"},
                },
            )
        if path == "/accounts/loan-uid-1/balances":
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balance_type": "CLBD",
                            "balance_amount": {"amount": reported, "currency": "EUR"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patch_client(provider, handler):
        conn = await provider.handle_oauth_callback("code-loan")
    acc = conn.accounts[0]
    # A LOAN account still maps to checking: loans on a bank connection are
    # not modelled yet. What matters here is that the provider passes the
    # reported balance through untouched, whatever the type.
    assert acc.type == "checking"
    assert acc.balance == Decimal(reported)


# ----- card balances (issue #624) -----


def _balance(balance_type: str, amount: str, currency: str = "PLN") -> dict:
    return {
        "balance_type": balance_type,
        "balance_amount": {"amount": amount, "currency": currency},
    }


def _card_credentials() -> dict:
    # Fresh per test: get_accounts caches account details in the credentials.
    return {"session_id": "sess-x", "valid_until": "2099-01-01T00:00:00Z"}


def _card_bank(balances: list[dict], *, details: dict | None = None):
    """An EB bank with one CARD account: session, details and balances."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-x":
            return httpx.Response(
                200,
                json={"status": "AUTHORIZED", "accounts_data": [{"uid": "card-1"}]},
            )
        if path == "/accounts/card-1/details":
            return httpx.Response(
                200,
                json={
                    "uid": "card-1",
                    "display_name": "WORLD MASTERCARD",
                    "currency": "PLN",
                    "cash_account_type": "CARD",
                    **(details or {}),
                },
            )
        if path == "/accounts/card-1/balances":
            return httpx.Response(200, json={"balances": balances})
        raise AssertionError(f"unexpected path {path}")

    return handler


@pytest.mark.asyncio
async def test_card_debt_is_what_the_limit_leaves_available(eb_keys):
    """mBank reports the credit limit as the booked balance and the real free
    credit as the available one (issue #624). Stored as the debt, that card
    owes the limit minus what is available (100), not the 4900 available."""
    provider = EnableBankingProvider()
    handler = _card_bank(
        [_balance("ITBD", "5000.00"), _balance("ITAV", "4900.00")],
        details={"credit_limit": {"amount": "5000.00", "currency": "PLN"}},
    )

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    assert card.type == "credit_card"
    assert card.balance == Decimal("100.00")
    assert card.available_credit == Decimal("4900.00")
    assert card.credit_limit == Decimal("5000.00")


@pytest.mark.asyncio
async def test_card_available_credit_without_a_limit_waits_for_the_sync(eb_keys):
    """When the bank sends no limit, the free credit comes out on its own so
    the sync can state the debt from the limit the account row holds."""
    provider = EnableBankingProvider()
    handler = _card_bank([_balance("ITAV", "4900.00")])

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    assert card.credit_limit is None
    assert card.available_credit == Decimal("4900.00")
    assert card.balance == Decimal("0")


@pytest.mark.asyncio
async def test_card_with_only_a_booked_balance_keeps_the_banks_sign(eb_keys):
    """EB signs a card from the holder's side: a negative booked figure is
    debt, which is what Securo stores."""
    provider = EnableBankingProvider()
    handler = _card_bank([_balance("CLBD", "-100.00")])

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    assert card.balance == Decimal("100.00")
    assert card.available_credit is None


@pytest.mark.asyncio
async def test_card_without_a_limit_keeps_a_booked_debt_signed_by_the_bank(eb_keys):
    """Free credit alone cannot state the debt when no limit is known, but a
    booked figure EB signs as money owed still does, so the card does not read
    as free while the sync waits for a limit."""
    provider = EnableBankingProvider()
    handler = _card_bank([_balance("CLBD", "-100.00"), _balance("ITAV", "4900.00")])

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    assert card.credit_limit is None
    assert card.balance == Decimal("100.00")
    assert card.available_credit == Decimal("4900.00")


@pytest.mark.asyncio
async def test_card_balance_without_a_usable_amount_is_not_the_whole_limit(eb_keys):
    """A balance entry that carries no amount says nothing about the card. Read
    as zero free credit it would show the whole limit spent."""
    provider = EnableBankingProvider()
    handler = _card_bank(
        [
            {"balance_type": "ITAV", "balance_amount": {"currency": "PLN"}},
            _balance("CLBD", "-100.00"),
        ],
        details={"credit_limit": {"amount": "5000.00", "currency": "PLN"}},
    )

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    # The booked figure states the debt on its own.
    assert card.balance == Decimal("100.00")
    assert card.available_credit is None


@pytest.mark.asyncio
async def test_card_balance_without_a_usable_amount_does_not_hide_the_other_type(eb_keys):
    """ITAV and CLAV mean the same thing, so an entry without an amount must
    not hide the usable one of the other type."""
    provider = EnableBankingProvider()
    handler = _card_bank(
        [
            {"balance_type": "ITAV", "balance_amount": {"currency": "PLN"}},
            _balance("CLAV", "4900.00"),
        ],
        details={"credit_limit": {"amount": "5000.00", "currency": "PLN"}},
    )

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    card = accounts[0]
    assert card.available_credit == Decimal("4900.00")
    assert card.balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_background_sync_does_not_claim_a_cached_credit_limit(eb_keys):
    """A background sync reads the cached details and never calls /details. It
    reports the free credit and no limit, so the debt is stated against the
    limit the account row holds and a cached figure cannot overwrite it."""
    provider = EnableBankingProvider()
    credentials = {
        "session_id": "sess-x",
        ACCOUNT_DETAILS_KEY: {
            "card-1": {
                "name": "WORLD MASTERCARD",
                "cash_account_type": "CARD",
                "currency": "PLN",
                "masked_number": "**** 4242",
                "credit_limit": "5000.00",
            }
        },
    }
    calls: list[str] = []
    inner = _card_bank([_balance("ITBD", "5000.00"), _balance("ITAV", "4900.00")])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return inner(request)

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(credentials)

    assert "/accounts/card-1/details" not in calls
    card = accounts[0]
    assert card.credit_limit is None
    assert card.available_credit == Decimal("4900.00")
    # The booked figure here is the limit itself, so it states no debt.
    assert card.balance == Decimal("0")


@pytest.mark.asyncio
async def test_checking_account_balance_reading_is_unchanged(eb_keys):
    """The card rule is card-only: on the same provider, a checking account
    keeps reading exactly as before (issue #624 asked for no bank-wide flip)."""
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-x":
            return httpx.Response(
                200,
                json={"status": "AUTHORIZED", "accounts_data": [{"uid": "acc-1"}]},
            )
        if path == "/accounts/acc-1/details":
            return httpx.Response(
                200,
                json={
                    "uid": "acc-1",
                    "display_name": "Girokonto",
                    "currency": "PLN",
                    "cash_account_type": "CACC",
                },
            )
        if path == "/accounts/acc-1/balances":
            return httpx.Response(
                200, json={"balances": [_balance("ITAV", "4900.00")]}
            )
        raise AssertionError(f"unexpected path {path}")

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_card_credentials())

    checking = accounts[0]
    assert checking.balance == Decimal("4900.00")
    assert checking.available_credit is None


@pytest.mark.asyncio
async def test_get_transactions_parses_nested_and_flat_shapes(eb_keys):
    """Both `transactions:{booked,pending}` and flat list must produce the
    same internal TransactionData list."""
    provider = EnableBankingProvider()

    nested_page = {
        "transactions": {
            "booked": [
                {
                    "entry_reference": "ref-1",
                    "transaction_amount": {"amount": "10.00", "currency": "EUR"},
                    "credit_debit_indicator": "DBIT",
                    "booking_date": "2026-05-10",
                    "remittance_information": ["Groceries"],
                },
            ],
            "pending": [
                {
                    "transaction_amount": {"amount": "5.50", "currency": "EUR"},
                    "credit_debit_indicator": "CRDT",
                    "booking_date": "2026-05-11",
                    "remittance_information": ["Refund"],
                },
            ],
        },
        "continuation_key": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounts/acc-1/transactions"
        return httpx.Response(200, json=nested_page)

    credentials = {"session_id_enc": None, "session_id": "sess-x", "valid_until": "2099-01-01T00:00:00Z"}
    with _patch_client(provider, handler):
        nested = await provider.get_transactions(credentials, "acc-1", date(2026, 5, 1))

    assert len(nested) == 2
    debit = next(t for t in nested if t.type == "debit")
    assert debit.amount == Decimal("10.00")
    assert debit.status == "posted"
    assert debit.external_id == _entry_reference_external_id(
        "ref-1",
        {"amount": "10.00", "currency": "EUR"},
        nested_page["transactions"]["booked"][0],
    )
    credit = next(t for t in nested if t.type == "credit")
    assert credit.status == "pending"
    # The last page carried no cursor, so the whole feed was read.
    assert provider.truncated_account_ids == set()

    flat_page = {
        "transactions": [
            {
                "status": "BOOK",
                "transaction_amount": {"amount": "10.00", "currency": "EUR"},
                "credit_debit_indicator": "DBIT",
                "booking_date": "2026-05-10",
                "remittance_information": ["Groceries"],
            },
            {
                "status": "PDNG",
                "transaction_amount": {"amount": "5.50", "currency": "EUR"},
                "credit_debit_indicator": "CRDT",
                "booking_date": "2026-05-11",
                "remittance_information": ["Refund"],
            },
        ],
        "continuation_key": "",
    }

    def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=flat_page)

    with _patch_client(provider, handler2):
        flat = await provider.get_transactions(credentials, "acc-1", date(2026, 5, 1))

    assert len(flat) == 2
    assert sorted(t.type for t in flat) == sorted(t.type for t in nested)
    assert {t.status for t in flat} == {"posted", "pending"}


@pytest.mark.asyncio
async def test_get_transactions_stops_on_repeated_continuation_key(eb_keys, caplog):
    """A provider cursor loop must not duplicate a page up to the safety cap."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []
    page = {
        "transactions": [
            {
                "entry_reference": "looped-tx-1",
                "status": "BOOK",
                "transaction_amount": {"amount": "10.00", "currency": "EUR"},
                "credit_debit_indicator": "DBIT",
                "booking_date": "2026-05-10",
            }
        ],
        "continuation_key": "cursor-a",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=page)

    credentials = {
        "session_id": "sess-x",
        "valid_until": "2099-01-01T00:00:00Z",
    }
    with _patch_client(provider, handler), caplog.at_level("WARNING"):
        transactions = await provider.get_transactions(
            credentials, "acc-1", date(2026, 5, 1)
        )

    assert len(requests) == 2
    assert requests[0].url.params.get("continuation_key") is None
    assert requests[1].url.params["continuation_key"] == "cursor-a"
    expected_id = _entry_reference_external_id(
        "looped-tx-1",
        {"amount": "10.00", "currency": "EUR"},
        page["transactions"][0],
    )
    assert [transaction.external_id for transaction in transactions] == [expected_id]
    assert "pagination loop detected" in caplog.text
    # The loop hid whatever came after the repeated cursor.
    assert provider.truncated_account_ids == {"acc-1"}


@pytest.mark.asyncio
async def test_get_transactions_marks_account_truncated_at_page_cap(eb_keys, caplog):
    """Pages that still carry a cursor at the safety cap leave the rest of the
    feed unread, so the account is reported as truncated."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "transactions": [],
            "continuation_key": f"cursor-{len(requests)}",
        })

    credentials = {
        "session_id": "sess-x",
        "valid_until": "2099-01-01T00:00:00Z",
    }
    with (
        patch("app.providers.enable_banking.TRANSACTION_PAGE_LIMIT", 3),
        _patch_client(provider, handler),
        caplog.at_level("WARNING"),
    ):
        await provider.get_transactions(credentials, "acc-1", date(2026, 5, 1))

    assert len(requests) == 3
    assert provider.truncated_account_ids == {"acc-1"}
    assert "more than 3 transaction pages" in caplog.text


_WRONG_PERIOD_BODY = {
    "code": 422,
    "message": "Wrong transactions period requested",
    "detail": {"message": "Requested time period out of bound."},
    "error": "WRONG_TRANSACTIONS_PERIOD",
}
_CREDENTIALS = {"session_id": "sess-x", "valid_until": "2099-01-01T00:00:00Z"}


def _span_days(request: httpx.Request) -> int:
    """Inclusive number of days covered by a transactions request."""
    start = date.fromisoformat(request.url.params["date_from"])
    end = date.fromisoformat(request.url.params["date_to"])
    return (end - start).days + 1


@pytest.mark.asyncio
async def test_get_transactions_default_window_is_90_days_inclusive(eb_keys):
    """A background fetch (no user present) keeps the ordinary 90-day window;
    the longest history is only asked for on the user's own request."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"transactions": []})

    with _patch_client(provider, handler):
        await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 1
    assert requests[0].url.params["date_to"] == _utc_today().isoformat()
    assert requests[0].url.params.get("strategy") is None
    assert _span_days(requests[0]) == DEFAULT_HISTORY_DAYS == 90


_USER_PRESENT_HEADERS = {
    "Psu-Ip-Address": "203.0.113.7",
    "Psu-User-Agent": "Mozilla/5.0 (Securo test)",
}


def _user_present():
    """The fetch is driven by the user's own request, PSU headers and all."""
    return psu_present(PsuContext(headers=dict(_USER_PRESENT_HEADERS)))


def _utc_today() -> date:
    """The day the provider itself uses, so tests do not depend on the host zone."""
    return datetime.now(timezone.utc).date()


def _booked_entry(reference: str, *, days_ago: int) -> dict:
    """One booked entry of the bank's own payload, dated in the past."""
    return {
        "entry_reference": reference,
        "status": "BOOK",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": (_utc_today() - timedelta(days=days_ago)).isoformat(),
        "remittance_information": [reference],
    }


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_asks_for_the_longest_history(eb_keys):
    """A fresh fetch made with the user present asks Enable Banking for the
    longest period the bank serves, bounded by INITIAL_HISTORY_DAYS, and does
    not pick the end date itself (issue #792)."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"transactions": [_booked_entry("tx-old", days_ago=400)]}
        )

    with _patch_client(provider, handler), _user_present():
        transactions = await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 1
    params = requests[0].url.params
    assert params["strategy"] == "longest"
    assert "date_to" not in params
    assert params["date_from"] == (
        _utc_today() - timedelta(days=INITIAL_HISTORY_DAYS - 1)
    ).isoformat()
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_reads_every_page(eb_keys):
    """The paged answer is collected to the last page: the bank's cursor is
    followed, each page is part of the same longest request."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []
    pages = [
        {
            "transactions": [_booked_entry("tx-new", days_ago=3)],
            "continuation_key": "page-2",
        },
        {
            "transactions": [_booked_entry("tx-mid", days_ago=400)],
            "continuation_key": "page-3",
        },
        {
            "transactions": [_booked_entry("tx-old", days_ago=1200)],
            "continuation_key": "",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    with _patch_client(provider, handler), _user_present():
        transactions = await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 3
    assert requests[0].url.params.get("continuation_key") is None
    assert requests[1].url.params["continuation_key"] == "page-2"
    assert requests[2].url.params["continuation_key"] == "page-3"
    assert [r.url.params.get("strategy") for r in requests] == ["longest"] * 3
    assert {r.url.params["date_from"] for r in requests} == {
        (_utc_today() - timedelta(days=INITIAL_HISTORY_DAYS - 1)).isoformat()
    }
    assert len(transactions) == 3
    assert provider.truncated_account_ids == set()


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_pages_past_the_incremental_cap(eb_keys):
    """The initial cap is wider than the incremental one: a busy account's
    history is not cut at the 50th page, where what is left out would never be
    fetched again (later syncs only move forward)."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) >= 60:
            return httpx.Response(200, json={"transactions": []})
        return httpx.Response(
            200,
            json={"transactions": [], "continuation_key": f"cursor-{len(requests)}"},
        )

    with _patch_client(provider, handler), _user_present():
        await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 60
    assert provider.truncated_account_ids == set()


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_stops_at_its_page_cap(eb_keys, caplog):
    """Even the wider cap ends the loop: a feed that never stops paging is cut
    and the account is reported as truncated."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []
    cap = 3

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"transactions": [], "continuation_key": f"cursor-{len(requests)}"},
        )

    with (
        patch("app.providers.enable_banking.INITIAL_TRANSACTION_PAGE_LIMIT", cap),
        _patch_client(provider, handler),
        _user_present(),
        caplog.at_level("WARNING"),
    ):
        await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert cap < INITIAL_TRANSACTION_PAGE_LIMIT
    assert len(requests) == cap
    assert provider.truncated_account_ids == {"acc-1"}
    assert f"more than {cap} transaction pages" in caplog.text


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_falls_back_when_longest_is_refused(
    eb_keys,
):
    """A bank that rejects the longest period still gets the ordinary window:
    the connection is not failed over an attempt at more history."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("strategy") == "longest":
            return httpx.Response(422, json=_WRONG_PERIOD_BODY)
        return httpx.Response(
            200, json={"transactions": [_booked_entry("tx-1", days_ago=10)]}
        )

    with _patch_client(provider, handler), _user_present():
        transactions = await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert [r.url.params.get("strategy") for r in requests] == ["longest", None]
    assert _span_days(requests[1]) == DEFAULT_HISTORY_DAYS
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_falls_back_when_the_strategy_is_refused(
    eb_keys,
):
    """A refusal that is not about the period (a bank without the strategy)
    gets the same answer as a refused period: the ordinary window."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("strategy") == "longest":
            return httpx.Response(
                400,
                json={
                    "code": 400,
                    "error": "ASPSP_ERROR",
                    "message": "strategy is not supported",
                },
            )
        return httpx.Response(200, json={"transactions": []})

    with _patch_client(provider, handler), _user_present():
        transactions = await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert transactions == []
    assert [r.url.params.get("strategy") for r in requests] == ["longest", None]
    assert _span_days(requests[1]) == DEFAULT_HISTORY_DAYS


@pytest.mark.parametrize("status", [408, 503])
@pytest.mark.asyncio
async def test_get_transactions_initial_fetch_surfaces_a_transient_failure(
    eb_keys, status
):
    """A failing bank (a timeout or a server error) is not a refusal of the
    longest history: the error reaches the sync so it is retried, instead of
    a shorter history being reported as a complete one and the rest never
    asked for again."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status, json={"code": status, "message": "temporarily unavailable"}
        )

    with (
        _patch_client(provider, handler),
        _user_present(),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 1
    assert requests[0].url.params["strategy"] == "longest"


@pytest.mark.asyncio
async def test_get_transactions_incremental_fetch_never_asks_for_the_longest(eb_keys):
    """A sync that already has a starting point stays on its own window: the
    longest strategy is for the first, full fetch only."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"transactions": []})

    with _patch_client(provider, handler), _user_present():
        await provider.get_transactions(
            _CREDENTIALS, "acc-1", _utc_today() - timedelta(days=5)
        )

    assert len(requests) == 1
    assert requests[0].url.params.get("strategy") is None
    assert _span_days(requests[0]) == 6


@pytest.mark.asyncio
async def test_get_transactions_retries_shorter_window_on_wrong_period(eb_keys):
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if _span_days(request) > FALLBACK_HISTORY_DAYS:
            return httpx.Response(422, json=_WRONG_PERIOD_BODY)
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "entry_reference": "tx-1",
                        "status": "BOOK",
                        "transaction_amount": {"amount": "5.00", "currency": "EUR"},
                        "credit_debit_indicator": "DBIT",
                        "booking_date": date.today().isoformat(),
                    }
                ]
            },
        )

    with _patch_client(provider, handler):
        transactions = await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert [_span_days(r) for r in requests] == [
        DEFAULT_HISTORY_DAYS,
        FALLBACK_HISTORY_DAYS,
    ]
    expected_id = _entry_reference_external_id(
        "tx-1",
        {"amount": "5.00", "currency": "EUR"},
        {"booking_date": date.today().isoformat(), "credit_debit_indicator": "DBIT"},
    )
    assert [t.external_id for t in transactions] == [expected_id]


@pytest.mark.asyncio
async def test_get_transactions_wrong_period_with_explicit_since_retries_once(eb_keys):
    """A refused ``since`` older than 90 days is retried once with the last 90
    days (the cut is logged and kept for a later sync); when that is refused
    too, the error is the caller's, with no further shortening."""
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(422, json=_WRONG_PERIOD_BODY)

    with _patch_client(provider, handler), pytest.raises(httpx.HTTPStatusError):
        await provider.get_transactions(
            _CREDENTIALS, "acc-1", date.today() - timedelta(days=200)
        )

    assert len(requests) == 2
    retry = requests[1].url.params
    retry_days = (
        date.fromisoformat(retry["date_to"]) - date.fromisoformat(retry["date_from"])
    ).days + 1
    assert retry_days == 90


@pytest.mark.asyncio
async def test_get_transactions_other_422_is_not_retried(eb_keys):
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(422, json={"code": 422, "error": "SOMETHING_ELSE"})

    with _patch_client(provider, handler), pytest.raises(httpx.HTTPStatusError):
        await provider.get_transactions(_CREDENTIALS, "acc-1")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_refresh_credentials_expired_raises(eb_keys):
    provider = EnableBankingProvider()
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace(
        "+00:00", "Z"
    )
    with pytest.raises(SessionExpiredError):
        await provider.refresh_credentials({"valid_until": expired})


@pytest.mark.asyncio
async def test_refresh_credentials_valid_passes(eb_keys):
    provider = EnableBankingProvider()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )
    creds = {"valid_until": future, "session_id_enc": "enc"}
    out = await provider.refresh_credentials(creds)
    assert out is creds


@pytest.mark.asyncio
async def test_get_accounts_raises_session_expired_when_details_unauthorized(eb_keys):
    """A 401 on one account's /details kills the whole consent, not just it.

    ``_request`` maps 401/410 to SessionExpiredError precisely because EB's
    access token is session-scoped: the consent is dead for every account. The
    per-account ``except httpx.HTTPError`` must not swallow it; doing so stored
    an empty account list and left the connection "active" (issue #1013).
    """
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-x":
            return httpx.Response(200, json={
                "session_id": "sess-x",
                "accounts_data": [{"uid": "acc-1"}, {"uid": "acc-2"}],
            })
        # Anchored on purpose: ``endswith`` would also match a deeper path such
        # as /accounts/a/b/details, and ``.+`` in the middle matches it too.
        if re.fullmatch(r"/accounts/[^/]+/details", request.url.path):
            return httpx.Response(401, json={"code": 401, "error": "INVALID_TOKEN"})
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_client(provider, handler):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts(_CREDENTIALS)


@pytest.mark.asyncio
async def test_get_accounts_raises_when_the_bank_refuses_every_account(eb_keys):
    """An authorized session whose accounts all fail must not look like a sync.

    Returning an empty list here left the connection "active" with a fresh
    last_sync_at while its balances went stale.
    """
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-x":
            return httpx.Response(200, json={
                "session_id": "sess-x",
                "status": "AUTHORIZED",
                "accounts_data": [{"uid": "acc-1"}, {"uid": "acc-2"}],
            })
        if re.fullmatch(r"/accounts/[^/]+/details", request.url.path):
            return httpx.Response(400, json={"code": 400, "error": "ASPSP_ERROR"})
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_client(provider, handler):
        with pytest.raises(ProviderUserActionRequired) as excinfo:
            await provider.get_accounts(_CREDENTIALS)
    assert excinfo.value.code == "accounts_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failures, expected",
    [
        pytest.param({"acc-1": 503, "acc-2": 503}, 503, id="outage"),
        pytest.param({"acc-1": 408, "acc-2": 408}, 408, id="timeout"),
        pytest.param({"acc-1": 400, "acc-2": 503}, 503, id="refusal-and-outage"),
        pytest.param({"acc-1": 503, "acc-2": 400}, 503, id="outage-and-refusal"),
        pytest.param({"acc-1": "network", "acc-2": "network"}, "network", id="network"),
    ],
)
async def test_get_accounts_does_not_ask_for_a_reconnect_during_an_outage(
    eb_keys, failures, expected
):
    """Only refusals from the bank are the user's to act on; the rest is retryable."""
    provider = EnableBankingProvider()
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-x":
            return httpx.Response(200, json={
                "session_id": "sess-x",
                "status": "AUTHORIZED",
                "accounts_data": [{"uid": "acc-1"}, {"uid": "acc-2"}],
            })
        match = re.fullmatch(r"/accounts/([^/]+)/details", request.url.path)
        if not match:
            raise AssertionError(f"unexpected path {request.url.path}")
        attempted.append(match.group(1))
        failure = failures[match.group(1)]
        if failure == "network":
            raise httpx.ConnectError("no route to host", request=request)
        return httpx.Response(failure, json={"code": failure})

    with _patch_client(provider, handler):
        with pytest.raises(httpx.HTTPError) as excinfo:
            await provider.get_accounts(_CREDENTIALS)
    if expected == "network":
        assert isinstance(excinfo.value, httpx.ConnectError)
    else:
        assert isinstance(excinfo.value, httpx.HTTPStatusError)
        assert excinfo.value.response.status_code == expected
    assert attempted == ["acc-1", "acc-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 408, 503])
async def test_get_accounts_skips_a_single_failed_account(eb_keys, status):
    """One failed account stays a skip, whatever the failure: the others still sync."""
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-x":
            return httpx.Response(200, json={
                "session_id": "sess-x",
                "status": "AUTHORIZED",
                "accounts_data": [{"uid": "acc-1"}, {"uid": "acc-2"}],
            })
        if path == "/accounts/acc-1/details":
            return httpx.Response(status, json={"code": status})
        if path == "/accounts/acc-2/details":
            return httpx.Response(200, json={"uid": "acc-2", "currency": "NOK"})
        if path == "/accounts/acc-2/balances":
            return httpx.Response(200, json={"balances": []})
        raise AssertionError(f"unexpected path {path}")

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_CREDENTIALS)
    assert [account.external_id for account in accounts] == ["acc-2"]


@pytest.mark.asyncio
async def test_get_accounts_exposes_stable_external_id(eb_keys):
    """EB's `uid` is session-scoped; `identification_hash` is the stable key.

    Reauthorising mints a new session, so the same real account comes back with
    a new uid. The hash is what survives that, and it already rides in the
    /sessions payload this method fetches. Carry it out on AccountData so the
    sync can re-match the existing row instead of creating a duplicate.
    """
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-x":
            return httpx.Response(200, json={
                "session_id": "sess-x",
                "accounts_data": [
                    {
                        "uid": "acc-1",
                        "identification_hash": "hash-1",
                        "identification_hashes": ["hash-1"],
                    },
                ],
            })
        if path == "/accounts/acc-1/details":
            return httpx.Response(200, json={
                "uid": "acc-1",
                "display_name": "Main",
                "currency": "EUR",
                "cash_account_type": "CACC",
            })
        if path == "/accounts/acc-1/balances":
            return httpx.Response(200, json={"balances": []})
        raise AssertionError(f"unexpected path {path}")

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts(_CREDENTIALS)

    assert len(accounts) == 1
    assert accounts[0].external_id == "acc-1"
    assert accounts[0].stable_external_id == "hash-1"



# ----- account identifier / masking (issue #408) -----


def test_account_identifier_prefers_iban():
    raw = {
        "account_id": {"iban": "NL91ABNA0417164300", "other": {"identification": "999"}},
        "all_account_ids": [{"identification": "888", "scheme_name": "BBAN"}],
    }
    assert _account_identifier(raw) == "NL91ABNA0417164300"


def test_account_identifier_falls_back_to_other_scheme():
    """Banks outside SEPA report no IBAN; we must still find an identifier."""
    raw = {"account_id": {"other": {"identification": "12345678", "scheme_name": "BBAN"}}}
    assert _account_identifier(raw) == "12345678"


def test_account_identifier_falls_back_to_all_account_ids():
    raw = {"all_account_ids": [{"identification": "87654321", "scheme_name": "BBAN"}]}
    assert _account_identifier(raw) == "87654321"


def test_account_identifier_returns_none_when_absent():
    assert _account_identifier({"uid": "abc", "product": "Girokonto"}) is None
    assert _account_identifier({"account_id": {}, "all_account_ids": []}) is None


def test_mask_last4_keeps_only_the_tail():
    # The whole point: the full IBAN never reaches the database.
    assert mask_last4("NL91ABNA0417164300") == "4300"


def test_mask_last4_ignores_separators():
    """IBANs are commonly formatted in groups of four."""
    assert mask_last4("NL91 ABNA 0417 1643 00") == "4300"
    assert mask_last4("1234-5678") == "5678"


def test_mask_last4_returns_none_when_unusable():
    assert mask_last4(None) is None
    assert mask_last4("") is None
    # Too short to mask: render nothing rather than a partial identifier.
    assert mask_last4("12") is None
    assert mask_last4("- -") is None


def test_mask_last4_handles_exactly_four():
    assert mask_last4("1234") == "1234"


# ----- direction without credit_debit_indicator (issue #937) -----


def _signed_entry(amount: str, indicator: str | None) -> dict:
    raw: dict = {
        "transaction_amount": {"amount": amount, "currency": "SEK"},
        "booking_date": "2026-08-20",
        "remittance_information": [],
        "creditor": {"name": "ICA Kvantum"},
        "debtor": {"name": "Account Holder"},
    }
    if indicator is not None:
        raw["credit_debit_indicator"] = indicator
    return raw


@pytest.mark.parametrize(
    ("amount", "indicator", "expected"),
    [
        ("-249.00", None, "debit"),
        ("-249.00", "", "debit"),
        ("249.00", None, "credit"),
        ("249.00", "DBIT", "debit"),
        ("249.00", "CRDT", "credit"),
    ],
)
def test_build_transaction_direction(amount, indicator, expected):
    """A bank that sends no indicator signs the amount instead (SEB's reserved
    card purchases): a negative amount is money out. The amount is stored
    unsigned either way, and the payee is the party across the direction."""
    tx = EnableBankingProvider()._build_transaction(
        "acc-1", _signed_entry(amount, indicator), "posted", "auto"
    )
    assert tx is not None
    assert (tx.type, tx.amount) == (expected, Decimal("249.00"))
    assert tx.description == ("ICA Kvantum" if expected == "debit" else "Account Holder")


def test_build_transaction_reserved_and_booked_purchase_agree():
    """SEB lists the reservation signed with no indicator and the booking with
    DBIT; both are the same purchase, so both are debits."""
    provider = EnableBankingProvider()
    reserved = provider._build_transaction(
        "acc-1", _signed_entry("-249.00", None), "pending", "auto"
    )
    booked = provider._build_transaction(
        "acc-1", _signed_entry("249.00", "DBIT"), "posted", "auto"
    )
    assert reserved is not None and booked is not None
    assert (reserved.type, reserved.amount) == (booked.type, booked.amount)


# ----- counterparty fallback / transaction_date (issue #734, #753) -----


def test_build_transaction_description_falls_back_to_creditor_for_debit():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-20",
        "remittance_information": [],
        "creditor": {"name": "Example Merchant"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Example Merchant"


def test_build_transaction_description_falls_back_to_debtor_for_credit():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "100.00", "currency": "EUR"},
        "credit_debit_indicator": "CRDT",
        "booking_date": "2026-08-20",
        "remittance_information": [],
        "debtor": {"name": "Employer Inc"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Employer Inc"


def test_build_transaction_description_prefers_remittance_over_counterparty():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "15.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-20",
        "remittance_information": ["Order #12345"],
        "creditor": {"name": "Example Merchant"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Order #12345"


def test_build_transaction_description_prefers_additional_information_over_counterparty():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "15.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-20",
        "remittance_information": [],
        "additional_information": "Invoice 987",
        "creditor": {"name": "Example Merchant"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Invoice 987"


def test_build_transaction_description_whitespace_additional_information_falls_back():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "15.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-20",
        "remittance_information": [],
        "additional_information": "   ",
        "creditor": {"name": "Example Merchant"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Example Merchant"


def test_build_transaction_description_falls_back_to_generic_transaction():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "15.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-20",
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.description == "Transaction"


# ----- payee comes from the counterparty only (issue #1042) -----


def _payee_raw(indicator: str, creditor: str | None, debtor: str | None) -> dict:
    return {
        "transaction_amount": {"amount": "7.12", "currency": "EUR"},
        "credit_debit_indicator": indicator,
        "booking_date": "2026-09-28",
        "remittance_information": ["SUP BTC"],
        "creditor": {"name": creditor} if creditor else None,
        "debtor": {"name": debtor} if debtor else None,
    }


@pytest.mark.parametrize(
    ("indicator", "creditor", "debtor", "expected"),
    [
        # The #1042 payloads: the bank fills the holder's side and leaves
        # the counterparty out, so there is no payee to take.
        ("DBIT", None, "HOLDER", None),
        ("CRDT", "HOLDER", None, None),
        # With the counterparty present, it is the payee.
        ("DBIT", "MUZEUM NEONOW", "HOLDER", "MUZEUM NEONOW"),
        ("CRDT", "HOLDER", "Employer Inc", "Employer Inc"),
    ],
)
def test_build_transaction_payee_reads_only_the_counterparty(
    indicator, creditor, debtor, expected
):
    provider = EnableBankingProvider()
    tx = provider._build_transaction(
        "acc-1", _payee_raw(indicator, creditor, debtor), "posted", "auto"
    )
    assert tx is not None
    assert tx.payee == expected


@pytest.mark.parametrize("source", ["none", "description"])
def test_build_transaction_payee_off_when_the_connection_says_so(source):
    provider = EnableBankingProvider()
    tx = provider._build_transaction(
        "acc-1", _payee_raw("DBIT", "MUZEUM NEONOW", "HOLDER"), "posted", source
    )
    assert tx is not None
    assert tx.payee is None


def test_build_transaction_falls_back_to_transaction_date():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-08-29",
        "creditor": {"name": "Coffee Shop"},
    }
    tx = provider._build_transaction("acc-1", raw, "pending", "auto")
    assert tx is not None
    assert tx.date == date(2026, 8, 29)
    assert tx.description == "Coffee Shop"


def test_build_transaction_prefers_transaction_date_over_booking_date():
    """#790: a card purchase belongs to the day it happened, not the day it booked.

    The fixture mirrors the reported card entry: the purchase on 4 June was
    booked on 2 September, so booking first moved three months of spending
    onto a single statement date.
    """
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "46.88", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-09-02",
        "value_date": "2026-09-01",
        "transaction_date": "2026-06-04",
        "remittance_information": ["CARD PURCHASE"],
        "creditor": {"name": "Shop"},
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.date == date(2026, 6, 4)
    # The booking date stays in the stored payload, it is only not the date.
    assert tx.raw_data is not None
    assert tx.raw_data["booking_date"] == "2026-09-02"


def test_build_transaction_falls_back_to_booking_date_without_transaction_date():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-09-02",
        "value_date": "2026-09-01",
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.date == date(2026, 9, 2)


def test_build_transaction_falls_back_to_value_date_without_the_other_two():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": None,
        "value_date": "2026-09-01",
        "transaction_date": None,
    }
    tx = provider._build_transaction("acc-1", raw, "posted", "auto")
    assert tx is not None
    assert tx.date == date(2026, 9, 1)


def test_date_choice_keeps_the_external_id_of_both_id_recipes():
    """The id hashes the three dates, so choosing another one moves no row.

    A row stored before this change was keyed from the same payload, so the
    next sync still finds it by id instead of importing a second copy.
    """
    provider = EnableBankingProvider()
    fallback_raw: dict[str, Any] = {
        "transaction_amount": {"amount": "12.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-09-02",
        "value_date": "2026-09-01",
        "transaction_date": "2026-06-04",
        "remittance_information": ["CARD PURCHASE"],
        "creditor_account": {"iban": "PT50000201231234567890154"},
    }
    tx = provider._build_transaction("acc-1", fallback_raw, "posted", "auto")
    assert tx is not None
    assert tx.external_id == _txn_fingerprint("acc-1", fallback_raw)

    reference_raw: dict[str, Any] = {
        **fallback_raw,
        "entry_reference": "auth-1",
    }
    referenced = provider._build_transaction("acc-1", reference_raw, "posted", "auto")
    assert referenced is not None
    assert referenced.external_id == _entry_reference_external_id(
        "auth-1", reference_raw["transaction_amount"], reference_raw
    )


def test_build_transaction_ignores_non_string_transaction_date():
    provider = EnableBankingProvider()
    raw = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": 20260829,
        "creditor": {"name": "Coffee Shop"},
    }
    assert provider._build_transaction("acc-1", raw, "pending", "auto") is None


def test_build_transaction_treats_entry_reference_zero_as_missing():
    provider = EnableBankingProvider()
    raw1 = {
        "entry_reference": "0",
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-08-29",
        "creditor": {"name": "Coffee Shop"},
    }
    raw2 = {
        "entry_reference": "0",
        "transaction_amount": {"amount": "40.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-08-29",
        "creditor": {"name": "Bakery"},
    }
    tx1 = provider._build_transaction("acc-1", raw1, "pending", "auto")
    tx2 = provider._build_transaction("acc-1", raw2, "pending", "auto")
    assert tx1 is not None and tx2 is not None
    assert tx1.external_id != "0"
    assert tx2.external_id != "0"
    assert tx1.external_id != tx2.external_id


def test_build_transaction_disambiguates_shared_batch_entry_reference():
    """#753 follow-up: a shared settlement/batch reference must not collide.

    An Austrian bank reuses the same entry_reference for a fee line and its
    related main charge. Both carry a booking and a value date, so the #756
    fallback for an empty or "0" reference never applies. The fixture
    mirrors the reported pair: same reference, booking date and remittance
    text, different amounts and value dates.

    Wise reuses the card-authorization id the same way for every follow-on
    charge against one authorization, which this pair covers as well.
    """
    provider = EnableBankingProvider()
    fee = {
        "entry_reference": "ref-1",
        "transaction_amount": {"currency": "EUR", "amount": "0.55"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-31",
        "value_date": "2026-08-31",
        "remittance_information": ["E-COMM 1006,45 AT K3 26.08. 12:53"],
    }
    main_charge = {
        "entry_reference": "ref-1",
        "transaction_amount": {"currency": "EUR", "amount": "1006.45"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-31",
        "value_date": "2026-08-29",
        "remittance_information": ["E-COMM 1006,45 AT K3 26.08. 12:53"],
    }
    tx_fee = provider._build_transaction("acc-1", fee, "posted", "auto")
    tx_main = provider._build_transaction("acc-1", main_charge, "posted", "auto")
    assert tx_fee is not None and tx_main is not None
    assert tx_fee.external_id != tx_main.external_id
    assert tx_fee.amount == Decimal("0.55")
    assert tx_main.amount == Decimal("1006.45")


def test_build_transaction_entry_reference_external_id_stable_across_refetch():
    """A genuine re-fetch of the same booked transaction (identical payload)
    must still resolve to the same external_id, or every sync would
    re-insert it as new."""
    provider = EnableBankingProvider()
    raw = {
        "entry_reference": "ref-stable-1",
        "transaction_amount": {"currency": "EUR", "amount": "12.00"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-08-31",
        "value_date": "2026-08-31",
    }
    tx1 = provider._build_transaction("acc-1", raw, "posted", "auto")
    tx2 = provider._build_transaction("acc-1", dict(raw), "posted", "auto")
    assert tx1 is not None and tx2 is not None
    assert tx1.external_id == tx2.external_id


def test_entry_reference_external_id_differs_on_amount_or_date():
    base_amount = {"amount": "10.00", "currency": "EUR"}
    base_raw = {"booking_date": "2026-08-31", "value_date": "2026-08-31"}
    baseline = _entry_reference_external_id("ref-x", base_amount, base_raw)

    other_amount = _entry_reference_external_id(
        "ref-x", {"amount": "10.01", "currency": "EUR"}, base_raw
    )
    other_date = _entry_reference_external_id(
        "ref-x", base_amount, {"booking_date": "2026-09-01", "value_date": "2026-08-31"}
    )
    assert baseline != other_amount
    assert baseline != other_date


def test_entry_reference_external_id_stable_across_equivalent_amount_spellings():
    """"12.00" and "12.0" are the same Decimal amount.
    If we get 12.00 or 12.0 or 12 we need to calculate the same external id"""
    base_raw = {"booking_date": "2026-08-31", "value_date": "2026-08-31"}
    a = _entry_reference_external_id(
        "ref-x", {"amount": "12.00", "currency": "EUR"}, base_raw
    )
    b = _entry_reference_external_id(
        "ref-x", {"amount": "12.0", "currency": "EUR"}, base_raw
    )
    c = _entry_reference_external_id(
        "ref-x", {"amount": "12", "currency": "EUR"}, base_raw
    )
    assert a == b == c


def test_txn_fingerprint_includes_transaction_date():
    raw1 = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-08-29",
    }
    raw2 = {
        "transaction_amount": {"amount": "20.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-08-30",
    }
    fp1 = _txn_fingerprint("acc-1", raw1)
    fp2 = _txn_fingerprint("acc-1", raw2)
    assert fp1 != fp2


def test_entry_reference_external_id_differs_on_other_transaction_details():
    """A shared entry_reference with equal amount, currency and dates must
    still separate a charge from its refund and settlement lines for
    different counterparties."""
    amount = {"amount": "10.00", "currency": "EUR"}
    base_raw = {
        "booking_date": "2026-08-31",
        "value_date": "2026-08-31",
        "credit_debit_indicator": "DBIT",
        "remittance_information": ["Card purchase"],
        "creditor": {"name": "Shop A"},
    }
    baseline = _entry_reference_external_id("ref-x", amount, base_raw)

    refund = _entry_reference_external_id(
        "ref-x", amount, {**base_raw, "credit_debit_indicator": "CRDT"}
    )
    other_remittance = _entry_reference_external_id(
        "ref-x", amount, {**base_raw, "remittance_information": ["Card purchase 2"]}
    )
    other_creditor = _entry_reference_external_id(
        "ref-x", amount, {**base_raw, "creditor": {"name": "Shop B"}}
    )
    assert len({baseline, refund, other_remittance, other_creditor}) == 4
    assert baseline == _entry_reference_external_id("ref-x", amount, dict(base_raw))


# ----- session status on sync -----


def _session_payload(status: str) -> dict:
    """GET /sessions/{id} as Enable Banking returns it (account uids only).

    `valid_until` is always in the future: the status alone must decide."""
    valid_until = (
        (datetime.now(timezone.utc) + timedelta(days=180))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "status": status,
        "accounts": ["acc-uid-1"],
        "accounts_data": [{"uid": "acc-uid-1", "identification_hash": "hash-1"}],
        "aspsp": {"name": "Openbank", "country": "ES"},
        "access": {"valid_until": valid_until},
    }


@pytest.mark.asyncio
async def test_get_accounts_raises_when_session_expired_at_bank(eb_keys):
    """The bank can end consent long before `valid_until`. EB then still
    answers GET /sessions with 200 but `status: EXPIRED`, and every account
    call fails with a 400 ASPSP_ERROR. That must surface as an expired
    session, not as a sync that silently finds no accounts."""
    provider = EnableBankingProvider()
    details_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-1":
            return httpx.Response(200, json=_session_payload("EXPIRED"))
        details_calls.append(request.url.path)
        return httpx.Response(
            400,
            json={
                "code": 400,
                "message": "Error interacting with ASPSP",
                "detail": {"message": "Unauthorized, authentication failure"},
                "error": "ASPSP_ERROR",
            },
        )

    with _patch_client(provider, handler):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"session_id": "sess-1"})
    assert details_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["REVOKED", "CLOSED", "CANCELLED", "INVALID"])
async def test_get_accounts_raises_for_other_terminal_session_states(eb_keys, status):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_session_payload(status))

    with _patch_client(provider, handler):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"session_id": "sess-1"})


@pytest.mark.asyncio
async def test_get_accounts_authorized_session_returns_accounts(eb_keys):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-1":
            return httpx.Response(200, json=_session_payload("AUTHORIZED"))
        if path == "/accounts/acc-uid-1/details":
            return httpx.Response(
                200,
                json={
                    "uid": "acc-uid-1",
                    "name": "CUENTA CORRIENTE",
                    "currency": "EUR",
                    "cash_account_type": "CACC",
                    "account_id": {"iban": "ES0000000000000000001172"},
                },
            )
        if path == "/accounts/acc-uid-1/balances":
            return httpx.Response(
                200,
                json={"balances": [{
                    "balance_type": "CLBD",
                    "balance_amount": {"amount": "859.43", "currency": "EUR"},
                }]},
            )
        return httpx.Response(404)

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts({"session_id": "sess-1"})
    assert [a.external_id for a in accounts] == ["acc-uid-1"]
    assert accounts[0].balance == Decimal("859.43")


def _settling_payload(entry_ref: str, amount: str, *, booking_date: str | None) -> dict:
    return {
        "entry_reference": entry_ref,
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "booking_date": booking_date,
        "value_date": "2026-05-10",
        "remittance_information": ["SUPERMARKT 12 AT"],
    }


@pytest.mark.asyncio
async def test_get_transactions_drops_pending_twin_of_a_booked_entry(eb_keys):
    """An institution can list one operation as booked and still as pending
    while it settles. The booked entry is the truth; its pending twin would
    hash to a second id and land as a row that never clears."""
    provider = EnableBankingProvider()
    page = {
        "transactions": {
            "booked": [_settling_payload("auth-9", "25.00", booking_date="2026-05-11")],
            "pending": [_settling_payload("auth-9", "25.00", booking_date=None)],
        },
        "continuation_key": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    assert [(t.status, t.date) for t in txns] == [("posted", date(2026, 5, 11))]


@pytest.mark.asyncio
async def test_get_transactions_keeps_pending_siblings_sharing_a_reference(eb_keys):
    """Two equal charges on one card authorization share the reference (#753).
    One booking does not make the other pending entry its twin, so an
    ambiguous reference keeps every entry."""
    provider = EnableBankingProvider()
    page = {
        "transactions": {
            "booked": [_settling_payload("auth-1", "9.99", booking_date="2026-05-11")],
            "pending": [
                _settling_payload("auth-1", "9.99", booking_date=None),
                dict(
                    _settling_payload("auth-1", "9.99", booking_date=None),
                    value_date="2026-05-12",
                ),
            ],
        },
        "continuation_key": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    assert sorted(t.status for t in txns) == ["pending", "pending", "posted"]
    assert len({t.external_id for t in txns}) == 3


def _flat_entry(status: str, amount: str) -> dict:
    return {
        "status": status,
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }


@pytest.mark.asyncio
async def test_get_transactions_maps_every_flat_status(eb_keys):
    """A cancelled or rejected entry moved no money, so it is skipped; a held
    or scheduled one has not settled, so it is pending (#1232)."""
    provider = EnableBankingProvider()
    page = {
        "transactions": [
            _flat_entry("BOOK", "1.00"),
            _flat_entry("PDNG", "2.00"),
            _flat_entry("HOLD", "3.00"),
            _flat_entry("SCHD", "4.00"),
            _flat_entry("CNCL", "5.00"),
            _flat_entry("RJCT", "6.00"),
            _flat_entry("", "7.00"),
        ],
        "continuation_key": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    assert [(t.amount, t.status) for t in txns] == [
        (Decimal("1.00"), "posted"),
        (Decimal("2.00"), "pending"),
        (Decimal("3.00"), "pending"),
        (Decimal("4.00"), "pending"),
        (Decimal("7.00"), "posted"),
    ]


@pytest.mark.asyncio
async def test_get_transactions_keeps_identical_entries_of_one_day(eb_keys):
    """Two coffees of the same price on one day, listed pending with only the
    amount, the direction and the date, are two transactions. The first keeps
    the plain id, and a repeated page adds nothing (#1232)."""
    provider = EnableBankingProvider()
    coffee = {
        "transaction_amount": {"amount": "3.20", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }
    page = {
        "transactions": {"booked": [], "pending": [dict(coffee), dict(coffee)]},
        "continuation_key": "cursor-a",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    assert [t.status for t in txns] == ["pending", "pending"]
    assert txns[0].external_id == _txn_fingerprint("acc-1", coffee)
    assert txns[1].external_id != txns[0].external_id

    # The ids are the same on the next fetch.
    with _patch_client(provider, handler):
        again = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )
    assert [t.external_id for t in again] == [t.external_id for t in txns]


@pytest.mark.asyncio
async def test_get_transactions_keeps_identical_entries_split_across_pages(eb_keys):
    """Two identical purchases can straddle a page boundary, so the count of
    a repeated id runs over the whole fetch, not one page: both are imported,
    with the same ids on the next fetch (#1232)."""
    provider = EnableBankingProvider()
    coffee = {
        "transaction_amount": {"amount": "3.20", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }
    rented_bike = {
        "transaction_amount": {"amount": "1.10", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-09",
    }
    pages = [
        {
            "transactions": {"booked": [], "pending": [dict(rented_bike), dict(coffee)]},
            "continuation_key": "page-2",
        },
        {
            "transactions": {"booked": [], "pending": [dict(coffee)]},
            "continuation_key": "",
        },
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    assert len(requests) == 2
    assert [t.amount for t in txns] == [Decimal("1.10"), Decimal("3.20"), Decimal("3.20")]
    coffee_ids = [t.external_id for t in txns if t.amount == Decimal("3.20")]
    assert coffee_ids[0] == _txn_fingerprint("acc-1", coffee)
    assert coffee_ids[1] == _repeat_external_id(coffee_ids[0], 1)
    assert provider.truncated_account_ids == set()

    requests.clear()
    with _patch_client(provider, handler):
        again = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )
    assert [t.external_id for t in again] == [t.external_id for t in txns]


@pytest.mark.asyncio
async def test_get_transactions_drops_a_page_served_twice(eb_keys, caplog):
    """A broken cursor can serve a page already read with a fresh cursor
    instead of looping. Its entries must not land twice (#1232)."""
    provider = EnableBankingProvider()
    entry = _flat_entry("BOOK", "10.00")
    pages = [
        {
            "transactions": [dict(entry, entry_reference="ref-1")],
            "continuation_key": "page-2",
        },
        {
            "transactions": [dict(entry, entry_reference="ref-1")],
            "continuation_key": "page-3",
        },
        {
            "transactions": [dict(entry, entry_reference="ref-2")],
            "continuation_key": "",
        },
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    with _patch_client(provider, handler), caplog.at_level("WARNING"):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    # The repeated page ends the read: the page after it may well repeat too.
    assert len(requests) == 2
    assert [t.status for t in txns] == ["posted"]
    assert provider.truncated_account_ids == {"acc-1"}
    assert "pagination loop detected" in caplog.text


@pytest.mark.asyncio
async def test_get_transactions_keeps_a_page_whose_status_changed(eb_keys):
    """The same entry served first as pending and then as booked is new
    content, not a page repeated: the booked spelling is what settles the
    pending row (#1232)."""
    provider = EnableBankingProvider()
    charge = {
        "entry_reference": "auth-7",
        "transaction_amount": {"amount": "25.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }
    pages = [
        {
            "transactions": {"booked": [], "pending": [dict(charge)]},
            "continuation_key": "page-2",
        },
        {
            "transactions": {"booked": [dict(charge)], "pending": []},
            "continuation_key": "page-3",
        },
        {
            "transactions": {"booked": [_flat_entry("BOOK", "4.00")], "pending": []},
            "continuation_key": "",
        },
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    # The booked spelling settles the pending one listed on the page before,
    # so only the booked row reaches the sync.
    assert len(requests) == 3
    assert [t.status for t in txns] == ["posted", "posted"]
    assert [str(t.amount) for t in txns] == ["25.00", "4.00"]
    assert provider.truncated_account_ids == set()


@pytest.mark.asyncio
async def test_get_transactions_numbers_a_booked_entry_apart(eb_keys):
    """A pending spelling and the booked one it settles keep one fingerprint,
    and a bank can list both in the same response. The booked one is numbered
    apart, so it holds the id it gets as soon as the pending twin is gone
    (#1232)."""
    provider = EnableBankingProvider()
    coffee = {
        "transaction_amount": {"amount": "3.20", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }
    page = {
        "transactions": {"booked": [dict(coffee)], "pending": [dict(coffee)]},
        "continuation_key": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with _patch_client(provider, handler):
        txns = await provider.get_transactions(
            {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
        )

    base = _txn_fingerprint("acc-1", coffee)
    assert [(t.status, t.external_id) for t in txns] == [
        ("posted", base),
        ("pending", base),
    ]


@pytest.mark.asyncio
async def test_get_transactions_cancelled_entry_keeps_its_slot(eb_keys):
    """A cancelled entry is not imported, but it keeps its place among the
    identical entries of its page, so the one still listed keeps the id it had
    while both were pending, and its id is reported as still listed (#1232)."""
    provider = EnableBankingProvider()
    coffee = {
        "transaction_amount": {"amount": "3.20", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "transaction_date": "2026-05-10",
    }
    both_pending = [{**coffee, "status": "PDNG"}, {**coffee, "status": "PDNG"}]
    one_cancelled = [{**coffee, "status": "CNCL"}, {**coffee, "status": "PDNG"}]
    fetched = []
    for entries in (both_pending, one_cancelled):
        page = {"transactions": entries, "continuation_key": ""}

        def handler(request: httpx.Request, page=page) -> httpx.Response:
            return httpx.Response(200, json=page)

        with _patch_client(provider, handler):
            fetched.append(await provider.get_transactions(
                {"session_id": "sess-x"}, "acc-1", date(2026, 5, 1)
            ))

    before, after = fetched
    assert [t.status for t in after] == ["pending"]
    assert after[0].external_id == before[1].external_id
    assert provider.cancelled_transaction_ids == {"acc-1": {before[0].external_id}}
