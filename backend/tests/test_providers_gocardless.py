"""Unit tests for the GoCardless Bank Account Data provider."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers import gocardless as gocardless_module
from app.providers.base import (
    ProviderRateLimited,
    ProviderUserActionRequired,
    SessionExpiredError,
)
from app.providers.gocardless import (
    FALLBACK_ACCESS_VALID_FOR_DAYS,
    FALLBACK_MAX_HISTORICAL_DAYS,
    GoCardlessProvider,
    _txn_fingerprint,
)


@pytest.fixture(autouse=True)
def gocardless_settings(monkeypatch):
    monkeypatch.setenv("GOCARDLESS_SECRET_ID", "test-secret-id")
    monkeypatch.setenv("GOCARDLESS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("GOCARDLESS_API_URL", "https://bankaccountdata.gocardless.test/api/v2")
    from app.core.config import get_settings

    get_settings.cache_clear()
    GoCardlessProvider._cached_token = None
    GoCardlessProvider._cached_token_exp = 0.0
    yield
    get_settings.cache_clear()
    GoCardlessProvider._cached_token = None
    GoCardlessProvider._cached_token_exp = 0.0


def _patch_client(provider: GoCardlessProvider, handler, *, authenticated=True):
    transport = httpx.MockTransport(handler)
    if authenticated:
        GoCardlessProvider._cached_token = "test-access-token"
        GoCardlessProvider._cached_token_exp = time.time() + 3600

    def fake_client(token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.AsyncClient(
            base_url="https://bankaccountdata.gocardless.test/api/v2",
            transport=transport,
            headers=headers,
        )

    return patch.object(provider, "_client", side_effect=fake_client)


@pytest.mark.asyncio
async def test_access_token_mints_caches_and_remints_after_expiry():
    provider = GoCardlessProvider()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/v2/token/new/"
        token_number = len(requests)
        return httpx.Response(
            200,
            json={"access": f"token-{token_number}", "access_expires": 3600},
        )

    with _patch_client(provider, handler, authenticated=False):
        first = await provider._access_token()
        cached = await provider._access_token()
        GoCardlessProvider._cached_token_exp = time.time() + 599
        refreshed = await provider._access_token()

    assert first == cached == "token-1"
    assert refreshed == "token-2"
    assert len(requests) == 2
    assert requests[0].headers.get("authorization") is None
    assert requests[0].read() == (b'{"secret_id":"test-secret-id","secret_key":"test-secret-key"}')


@pytest.mark.asyncio
async def test_list_institutions_maps_fields_and_countries():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/institutions/"
        assert request.url.params["country"] == "gb"
        assert request.headers["authorization"] == "Bearer test-access-token"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "MONZO_MONZGB2L",
                    "name": "Monzo",
                    "countries": ["GB"],
                    "logo": "https://cdn.test/monzo.png",
                    "bic": "MONZGB2L",
                    "max_access_valid_for_days": "90",
                    "transaction_total_days": "730",
                }
            ],
        )

    with _patch_client(provider, handler):
        result = await provider.list_institutions("GB")

    assert result.countries == ["GB"]
    assert len(result.institutions) == 1
    institution = result.institutions[0]
    assert institution.name == "MONZO_MONZGB2L"
    assert institution.display_name == "Monzo"
    assert institution.country == "GB"
    assert institution.max_history_days == 730
    assert institution.max_consent_days == 90


@pytest.mark.asyncio
async def test_list_institutions_skips_entries_missing_id_or_name():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "MONZO_MONZGB2L",
                    "name": "Monzo",
                    "countries": ["GB"],
                },
                {"id": "X_NONAME", "countries": ["GB"]},
                {"id": "", "name": "Blank", "countries": ["FR"]},
            ],
        )

    with _patch_client(provider, handler):
        result = await provider.list_institutions()

    assert result.countries == ["GB"]
    assert len(result.institutions) == 1
    assert result.institutions[0].name == "MONZO_MONZGB2L"


@pytest.mark.asyncio
async def test_get_oauth_url_caps_consent_and_uses_state_as_reference():
    provider = GoCardlessProvider()
    payloads = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/institutions/MONZO_MONZGB2L/"):
            return httpx.Response(
                200,
                json={
                    "transaction_total_days": 730,
                    "max_access_valid_for_days": 90,
                    "supported_features": ["separate_continuous_history_consent"],
                },
            )
        if request.url.path.endswith("/agreements/enduser/"):
            payloads["agreement"] = request.read()
            return httpx.Response(201, json={"id": "agreement-1"})
        if request.url.path.endswith("/requisitions/"):
            payloads["requisition"] = request.read()
            return httpx.Response(201, json={"link": "https://bank.test/consent"})
        raise AssertionError(request.url)

    with _patch_client(provider, handler):
        link = await provider.get_oauth_url(
            "https://securo.test/oauth/callback",
            "signed-state",
            {"institution_name": "MONZO_MONZGB2L", "valid_until_days": 180},
        )

    import json

    agreement = json.loads(payloads["agreement"])
    requisition = json.loads(payloads["requisition"])
    assert link == "https://bank.test/consent"
    assert agreement == {
        "institution_id": "MONZO_MONZGB2L",
        "max_historical_days": 90,
        "access_valid_for_days": 90,
        "access_scope": ["balances", "details", "transactions"],
    }
    assert requisition == {
        "redirect": "https://securo.test/oauth/callback",
        "institution_id": "MONZO_MONZGB2L",
        "agreement": "agreement-1",
        "reference": "signed-state",
        "user_language": "en",
    }


@pytest.mark.asyncio
async def test_get_oauth_url_retries_once_with_agreement_fallback():
    provider = GoCardlessProvider()
    agreement_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/institutions/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "transaction_total_days": 365,
                    "max_access_valid_for_days": 180,
                    "supported_features": [],
                },
            )
        if request.url.path.endswith("/agreements/enduser/"):
            import json

            agreement_payloads.append(json.loads(request.read()))
            if len(agreement_payloads) == 1:
                return httpx.Response(400, json={"summary": "invalid maxima"})
            return httpx.Response(201, json={"id": "agreement-fallback"})
        if request.url.path.endswith("/requisitions/"):
            return httpx.Response(201, json={"link": "https://bank.test/fallback"})
        raise AssertionError(request.url)

    with _patch_client(provider, handler):
        result = await provider.get_oauth_url(
            "https://securo.test/oauth/callback",
            "state",
            {"institution_name": "BANK_ID"},
        )

    assert result == "https://bank.test/fallback"
    assert len(agreement_payloads) == 2
    assert agreement_payloads[0]["max_historical_days"] == 365
    assert agreement_payloads[1]["access_valid_for_days"] == (FALLBACK_ACCESS_VALID_FOR_DAYS)
    assert agreement_payloads[1]["max_historical_days"] == (FALLBACK_MAX_HISTORICAL_DAYS)


@pytest.mark.asyncio
async def test_handle_callback_rejects_non_linked_requisition():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [{"id": "req-1", "reference": "state", "status": "RJ"}],
                "next": None,
            },
        )

    with (
        _patch_client(provider, handler),
        pytest.raises(ProviderUserActionRequired, match="requisition status RJ") as exc_info,
    ):
        await provider.handle_oauth_callback("state")

    assert exc_info.value.code == "requisition_not_linked"


@pytest.mark.asyncio
async def test_handle_callback_pages_and_builds_connection_accounts():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/requisitions/"):
            offset = int(request.url.params["offset"])
            if offset == 0:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": f"other-{index}",
                                "reference": f"other-state-{index}",
                            }
                            for index in range(100)
                        ],
                        "next": "next-page",
                    },
                )
            assert offset == 100
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "req-linked",
                            "reference": "signed-state",
                            "status": "LN",
                            "agreement": "agr-1",
                            "institution_id": "NATIONWIDE_NAIAGB21",
                            "accounts": ["account-1"],
                        }
                    ],
                    "next": None,
                },
            )
        if path.endswith("/agreements/enduser/agr-1/"):
            return httpx.Response(
                200,
                json={
                    "accepted": "2026-01-01T12:00:00Z",
                    "access_valid_for_days": 90,
                    "max_historical_days": 365,
                },
            )
        if path.endswith("/institutions/NATIONWIDE_NAIAGB21/"):
            return httpx.Response(
                200,
                json={"name": "Nationwide", "logo": "https://cdn.test/nw.png"},
            )
        if path.endswith("/accounts/account-1/"):
            return httpx.Response(
                200,
                json={
                    "status": "READY",
                    "name": "Metadata account name",
                    "owner_name": "Metadata owner",
                    "iban": "GB00TEST12345678901234",
                },
            )
        if path.endswith("/accounts/account-1/details/"):
            return httpx.Response(
                200,
                json={
                    "account": {
                        "name": "Details account name",
                        "ownerName": "Details owner",
                        "cashAccountType": "SVGS",
                        "currency": "EUR",
                    }
                },
            )
        if path.endswith("/accounts/account-1/balances/"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balanceType": "interimAvailable",
                            "balanceAmount": {"amount": "20.00", "currency": "GBP"},
                        },
                        {
                            "balanceType": "closingBooked",
                            "balanceAmount": {"amount": "42.50", "currency": "GBP"},
                        },
                    ]
                },
            )
        raise AssertionError(request.url)

    with _patch_client(provider, handler):
        connection = await provider.handle_oauth_callback("signed-state")

    assert connection.external_id == "req-linked"
    assert connection.institution_name == "Nationwide"
    assert connection.logo_url == "https://cdn.test/nw.png"
    assert provider._requisition_id(connection.credentials) == "req-linked"
    assert connection.credentials["requisition_id_enc"] != "req-linked"
    assert connection.credentials["max_history_days"] == 365
    assert connection.credentials["valid_until"] == "2026-04-01T12:00:00+00:00"
    assert len(connection.accounts) == 1
    account = connection.accounts[0]
    assert account.name == "Metadata account name"
    assert account.type == "savings"
    assert account.balance == Decimal("42.50")
    assert account.currency == "GBP"
    assert account.masked_number == "1234"


@pytest.mark.asyncio
async def test_get_accounts_expired_requisition_raises():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/requisitions/req-expired/")
        return httpx.Response(200, json={"status": "EX", "accounts": []})

    with _patch_client(provider, handler), pytest.raises(SessionExpiredError):
        await provider.get_accounts({"requisition_id": "req-expired"})


@pytest.mark.asyncio
async def test_get_accounts_skips_suspended_account():
    provider = GoCardlessProvider()
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/requisitions/req-active/"):
            return httpx.Response(200, json={"status": "LN", "accounts": ["suspended"]})
        if request.url.path.endswith("/accounts/suspended/"):
            return httpx.Response(200, json={"status": "SUSPENDED"})
        raise AssertionError(request.url)

    with _patch_client(provider, handler):
        accounts = await provider.get_accounts({"requisition_id": "req-active"})

    assert accounts == []
    assert not any(path.endswith("/details/") for path in requested_paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_endpoint", "status", "body", "expected"),
    [
        ("details", 429, "daily account limit", ProviderRateLimited),
        ("details", 401, "End User Agreement abc expired", SessionExpiredError),
        ("balances", 429, "daily account limit", ProviderRateLimited),
        ("balances", 401, "End User Agreement abc expired", SessionExpiredError),
        ("balances", 500, "upstream failure", httpx.HTTPStatusError),
    ],
)
async def test_build_account_propagates_access_failures(
    failed_endpoint, status, body, expected
):
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts/X/"):
            return httpx.Response(200, json={"status": "READY"})
        if path.endswith("/accounts/X/details/"):
            if failed_endpoint == "details":
                return httpx.Response(status, text=body)
            return httpx.Response(
                200,
                json={"account": {"name": "Account", "currency": "GBP"}},
            )
        if path.endswith("/accounts/X/balances/"):
            return httpx.Response(status, text=body)
        raise AssertionError(request.url)

    with _patch_client(provider, handler), pytest.raises(expected):
        await provider._build_account("X")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [None, "not-a-number"])
async def test_build_account_skips_unparseable_balance(amount):
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts/X/"):
            return httpx.Response(200, json={"status": "READY"})
        if path.endswith("/accounts/X/details/"):
            return httpx.Response(
                200,
                json={"account": {"name": "Account", "currency": "GBP"}},
            )
        if path.endswith("/accounts/X/balances/"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balanceType": "closingBooked",
                            "balanceAmount": {"amount": amount, "currency": "GBP"},
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    with _patch_client(provider, handler):
        account = await provider._build_account("X")

    assert account is None


@pytest.mark.asyncio
async def test_get_transactions_maps_booked_pending_and_drops_dateless():
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/accounts/X/transactions/")
        assert request.url.params["date_from"] == "2026-08-01"
        return httpx.Response(
            200,
            json={
                "transactions": {
                    "booked": [
                        {
                            "transactionId": "T1",
                            "bookingDate": "2026-08-20",
                            "transactionAmount": {
                                "amount": "-12.50",
                                "currency": "GBP",
                            },
                            "creditorName": "TESCO",
                            "remittanceInformationUnstructured": "TESCO STORES",
                        },
                        {
                            "entryReference": "ENTRY-1",
                            "internalTransactionId": "INTERNAL-BOOKED",
                            "valueDate": "2026-08-21",
                            "transactionAmount": {
                                "amount": "100.00",
                                "currency": "GBP",
                            },
                            "debtorName": "Employer",
                            "remittanceInformationUnstructuredArray": [
                                "SALARY",
                                "AUGUST",
                            ],
                        },
                    ],
                    "pending": [
                        {
                            "internalTransactionId": "MUTABLE-PENDING-ID",
                            "bookingDate": "2026-08-22",
                            "transactionAmount": {
                                "amount": "-3.25",
                                "currency": "GBP",
                            },
                            "creditorName": "Cafe",
                        },
                        {
                            "transactionId": "NO-DATE",
                            "transactionAmount": {
                                "amount": "-1.00",
                                "currency": "GBP",
                            },
                        },
                    ],
                }
            },
        )

    with _patch_client(provider, handler):
        transactions = await provider.get_transactions(
            {"max_history_days": 90}, "X", since=date(2026, 8, 1)
        )

    assert len(transactions) == 3
    tesco = transactions[0]
    assert (
        tesco.external_id,
        tesco.type,
        tesco.amount,
        tesco.date,
        tesco.status,
        tesco.payee,
        tesco.currency,
    ) == (
        "T1",
        "debit",
        Decimal("12.50"),
        date(2026, 8, 20),
        "posted",
        "TESCO",
        "GBP",
    )
    assert transactions[1].external_id == "ENTRY-1"
    assert transactions[1].type == "credit"
    assert transactions[1].payee == "Employer"
    assert transactions[1].description == "SALARY AUGUST"
    pending = transactions[2]
    assert pending.status == "pending"
    assert pending.external_id != "MUTABLE-PENDING-ID"
    assert pending.raw_data is not None
    assert pending.external_id == _txn_fingerprint("X", pending.raw_data)


@pytest.mark.asyncio
async def test_nationwide_quirks_clamp_date_and_discard_malformed_ids(monkeypatch):
    provider = GoCardlessProvider()
    base_today = date.today()
    future = base_today + timedelta(days=7)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transactions": {
                    "booked": [],
                    "pending": [
                        {
                            "transactionId": "00DEBIT-mutable",
                            "bookingDate": future.isoformat(),
                            "transactionAmount": {
                                "amount": "-5.00",
                                "currency": "GBP",
                            },
                        },
                        {
                            "transactionId": "short-id",
                            "bookingDate": base_today.isoformat(),
                            "transactionAmount": {
                                "amount": "-6.00",
                                "currency": "GBP",
                            },
                        },
                    ],
                }
            },
        )

    with _patch_client(provider, handler):
        transactions = await provider.get_transactions(
            {"institution_id": "NATIONWIDE_NAIAGB21"}, "X"
        )

    assert len(transactions) == 2
    assert transactions[0].date == base_today
    for transaction in transactions:
        assert transaction.raw_data is not None
        assert "_fingerprintBookingDate" not in transaction.raw_data
    non_future = transactions[1]
    assert non_future.raw_data is not None
    assert non_future.external_id == _txn_fingerprint("X", non_future.raw_data)

    class FakeDate(date):
        @classmethod
        def today(cls):
            return base_today + timedelta(days=1)

    monkeypatch.setattr(gocardless_module, "date", FakeDate)
    with _patch_client(provider, handler):
        later_transactions = await provider.get_transactions(
            {"institution_id": "NATIONWIDE_NAIAGB21"}, "X"
        )

    assert later_transactions[0].date == base_today + timedelta(days=1)
    assert later_transactions[0].external_id == transactions[0].external_id
    for transaction in later_transactions:
        assert transaction.raw_data is not None
        assert "_fingerprintBookingDate" not in transaction.raw_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, "daily account limit", ProviderRateLimited),
        (401, "End User Agreement abc expired", SessionExpiredError),
    ],
)
async def test_request_maps_rate_limit_and_expired_agreement(status, body, expected):
    provider = GoCardlessProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    with _patch_client(provider, handler), pytest.raises(expected):
        await provider._request("GET", "/accounts/X/balances/")


@pytest.mark.asyncio
async def test_refresh_credentials_rejects_expired_consent():
    provider = GoCardlessProvider()
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    with pytest.raises(SessionExpiredError, match="consent expired"):
        await provider.refresh_credentials({"valid_until": expired})


def test_sync_stale_threshold_is_24_hours():
    assert GoCardlessProvider().sync_stale_threshold == timedelta(hours=24)


@pytest.mark.asyncio
async def test_sync_all_uses_provider_specific_stale_threshold(monkeypatch):
    from app.tasks import sync_tasks

    now = datetime.now(timezone.utc)
    gocardless_id = uuid.uuid4()
    enable_banking_id = uuid.uuid4()
    user_id = uuid.uuid4()
    rows = [
        (gocardless_id, user_id, (now - timedelta(hours=5)).replace(tzinfo=None), "gocardless"),
        (enable_banking_id, user_id, now - timedelta(hours=5), "enable_banking"),
    ]

    class Result:
        def all(self):
            return rows

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            return Result()

    class Engine:
        dispose = AsyncMock()

    def session_maker():
        return Session()

    thresholds = {
        "gocardless": timedelta(hours=24),
        "enable_banking": timedelta(hours=4),
    }
    monkeypatch.setattr(
        sync_tasks,
        "_make_session_maker",
        lambda: (Engine(), session_maker),
    )
    monkeypatch.setattr(
        sync_tasks,
        "get_provider",
        lambda name: SimpleNamespace(sync_stale_threshold=thresholds[name]),
    )
    sync_one = AsyncMock()
    monkeypatch.setattr(sync_tasks, "_sync_one", sync_one)

    synced = await sync_tasks._sync_all()

    assert synced == 1
    sync_one.assert_awaited_once_with(session_maker, enable_banking_id, user_id)
