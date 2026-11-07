"""Background Enable Banking syncs stay inside what banks serve without the user."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.providers.base import AccountData
from app.providers.enable_banking import ACCOUNT_DETAILS_KEY, EnableBankingProvider
from app.providers.psu import PsuContext, psu_present
from app.services.connection_service import sync_connection

USER = PsuContext(headers={"Psu-Ip-Address": "1.1.1.1", "Psu-User-Agent": "Firefox"})
DETAILS = {
    "uid": "acc-1",
    "currency": "NOK",
    "display_name": "Brukskonto",
    "cash_account_type": "CACC",
    "account_id": {"iban": "NO9386011117947"},
}


@pytest.fixture
def eb_keys(monkeypatch):
    monkeypatch.setattr(EnableBankingProvider, "_jwt_token", classmethod(lambda cls: "jwt"))


def _patch_client(provider, handler):
    transport = httpx.MockTransport(handler)

    def fake_client():
        return httpx.AsyncClient(base_url="https://api.enablebanking.com", transport=transport)

    return patch.object(provider, "_client", side_effect=fake_client)


def _bank(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/sessions/sess-1":
            return httpx.Response(200, json={"accounts_data": [{"uid": "acc-1"}]})
        if request.url.path == "/accounts/acc-1/details":
            return httpx.Response(200, json=DETAILS)
        if request.url.path == "/accounts/acc-1/balances":
            return httpx.Response(200, json={"balances": [
                {"balance_type": "CLBD", "balance_amount": {"amount": "10.00", "currency": "NOK"}},
            ]})
        return httpx.Response(404)

    return handler


def _credentials(cache=None):
    creds: dict = {"session_id": "sess-1", "aspsp": {"name": "Bank", "country": "NO"}}
    if cache is not None:
        creds[ACCOUNT_DETAILS_KEY] = cache
    return creds


@pytest.mark.asyncio
async def test_background_sync_reuses_cached_details(eb_keys):
    provider = EnableBankingProvider()
    calls: list[str] = []
    cache = {"acc-1": {"name": "Brukskonto", "cash_account_type": "CACC",
                       "currency": "NOK", "masked_number": "•••• 7947"}}
    with _patch_client(provider, _bank(calls)):
        [account] = await provider.get_accounts(_credentials(cache))

    assert "/accounts/acc-1/details" not in calls
    assert calls == ["/sessions/sess-1", "/accounts/acc-1/balances"]
    assert (account.name, account.type, account.currency) == ("Brukskonto", "checking", "NOK")
    assert account.masked_number == "•••• 7947"
    assert account.balance == Decimal("10.00")


@pytest.mark.asyncio
async def test_background_sync_fetches_and_caches_details_of_an_unknown_account(eb_keys):
    provider = EnableBankingProvider()
    calls: list[str] = []
    creds = _credentials()
    with _patch_client(provider, _bank(calls)):
        [account] = await provider.get_accounts(creds)

    assert "/accounts/acc-1/details" in calls
    assert account.name == "Brukskonto"
    cached = creds[ACCOUNT_DETAILS_KEY]["acc-1"]
    assert cached["name"] == "Brukskonto"
    assert cached["masked_number"] == account.masked_number
    # The cache keeps the mask, never the full account identifier.
    assert "NO9386011117947" not in str(cached)


@pytest.mark.asyncio
async def test_user_present_sync_reads_details_fresh(eb_keys):
    provider = EnableBankingProvider()
    calls: list[str] = []
    creds = _credentials({"acc-1": {"name": "Old name", "currency": "NOK"}})
    with _patch_client(provider, _bank(calls)), psu_present(USER):
        [account] = await provider.get_accounts(creds)

    assert "/accounts/acc-1/details" in calls
    assert account.name == "Brukskonto"
    assert creds[ACCOUNT_DETAILS_KEY]["acc-1"]["name"] == "Brukskonto"


@pytest.mark.asyncio
async def test_oauth_callback_caches_details_from_the_session(eb_keys):
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={
                "session_id": "sess-1", "accounts": [DETAILS],
                "aspsp": {"name": "Bank", "country": "NO"},
                "access": {"valid_until": "2027-01-01T00:00:00Z"},
            })
        return httpx.Response(200, json={"balances": [], "aspsps": []})

    with _patch_client(provider, handler):
        conn = await provider.handle_oauth_callback("code")

    assert conn.credentials[ACCOUNT_DETAILS_KEY]["acc-1"]["name"] == "Brukskonto"


def _window(calls: list[httpx.Request]) -> tuple[date, date]:
    params = calls[0].url.params
    return date.fromisoformat(params["date_from"]), date.fromisoformat(params["date_to"])


async def _fetch_since(since: date, context=None) -> tuple[date, date]:
    provider = EnableBankingProvider()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"transactions": {"booked": [], "pending": []}})

    with _patch_client(provider, handler), psu_present(context):
        await provider.get_transactions({"session_id": "s"}, "acc-1", since=since)
    return _window(calls)


@pytest.mark.asyncio
async def test_background_fetch_is_clamped_to_90_days(eb_keys):
    today = datetime.now(timezone.utc).date()
    start, end = await _fetch_since(today - timedelta(days=200))
    assert end == today
    assert (end - start).days + 1 == 90


@pytest.mark.asyncio
async def test_background_fetch_inside_90_days_is_unchanged(eb_keys):
    since = datetime.now(timezone.utc).date() - timedelta(days=20)
    start, _ = await _fetch_since(since)
    assert start == since


@pytest.mark.asyncio
async def test_user_present_fetch_is_not_clamped(eb_keys):
    since = datetime.now(timezone.utc).date() - timedelta(days=200)
    start, _ = await _fetch_since(since, context=USER)
    assert start == since


@pytest.mark.asyncio
async def test_sync_persists_credentials_the_provider_updated(
    session: AsyncSession, test_user, test_workspace,
):
    conn = BankConnection(
        user_id=test_user.id, workspace_id=test_workspace.id, provider="enable_banking",
        external_id="sess-1", institution_name="Bank", credentials={"token": "x"},
        status="active", last_sync_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    async def get_accounts(credentials):
        credentials[ACCOUNT_DETAILS_KEY] = {"acc-1": {"name": "Brukskonto"}}
        return [AccountData(external_id="acc-1", name="Brukskonto", type="checking",
                            balance=Decimal("1"), currency="NOK")]

    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(side_effect=lambda creds: creds)
    provider.get_accounts = AsyncMock(side_effect=get_accounts)
    provider.get_transactions = AsyncMock(return_value=[])
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    session.expunge_all()
    stored = await session.get(BankConnection, conn.id)
    assert stored is not None and stored.credentials is not None
    assert stored.credentials[ACCOUNT_DETAILS_KEY] == {"acc-1": {"name": "Brukskonto"}}
