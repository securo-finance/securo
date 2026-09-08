"""Unit tests for the Enable Banking provider.

Covers: JWT signing/claims, transaction fingerprint stability, account-type
mapping, nested vs flat transaction page shapes, restricted-mode handling.
HTTP is mocked end-to-end via httpx.MockTransport.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
    EnableBankingProvider,
    _account_identifier,
    _map_cash_account_type,
    _txn_fingerprint,
)


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
    assert debit.external_id == "ref-1"
    credit = next(t for t in nested if t.type == "credit")
    assert credit.status == "pending"

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
    assert [transaction.external_id for transaction in transactions] == ["looped-tx-1"]
    assert "pagination loop detected" in caplog.text


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
