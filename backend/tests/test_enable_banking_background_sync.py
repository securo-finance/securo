"""Background Enable Banking syncs stay inside what banks serve without the user."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.providers.base import AccountData, ProviderUserActionRequired
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


def _window(request: httpx.Request) -> tuple[date, date]:
    params = request.url.params
    return date.fromisoformat(params["date_from"]), date.fromisoformat(params["date_to"])


# How a bank refuses a window older than it serves: EB's own period error, or
# the reasonless ASPSP_ERROR a PSD2 bank gives a background call it does not
# allow (Sparebanken Vest's "Missing PSU-IP-Address header").
WRONG_PERIOD = httpx.Response(422, json={"error": "WRONG_TRANSACTIONS_PERIOD", "message": "x"})
PRESENCE_REFUSAL = httpx.Response(
    400, json={"code": 400, "error": "ASPSP_ERROR", "message": "Error interacting with ASPSP"}
)
TODAY = datetime.now(timezone.utc).date()
OLDEST_SERVED = TODAY - timedelta(days=89)


async def _fetch_since(since: date, context=None, required=None, refuse_before=None):
    """The windows requested, and the provider, for a fetch from `since`.

    `refuse_before` is the response the bank gives a window that starts
    before the last 90 days; None serves every window.
    """
    provider = EnableBankingProvider()
    windows: list[tuple[date, date]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        window = _window(request)
        windows.append(window)
        if refuse_before is not None and window[0] < OLDEST_SERVED:
            return refuse_before
        return httpx.Response(200, json={"transactions": {"booked": [], "pending": []}})

    credentials: dict = {"session_id": "s", "aspsp": {"name": "Bank"}}
    if required is not None:
        credentials["aspsp"]["required_psu_headers"] = required
    with _patch_client(provider, handler), psu_present(context):
        await provider.get_transactions(credentials, "acc-1", since=since)
    return windows, provider


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, USER])
async def test_a_long_window_the_bank_serves_is_not_cut(eb_keys, context):
    """The full window is tried first, for every bank."""
    since = TODAY - timedelta(days=200)
    windows, provider = await _fetch_since(since, context=context)
    assert windows == [(since, TODAY)]
    assert provider.incomplete_account_ids == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refusal", "required"),
    [(WRONG_PERIOD, None), (PRESENCE_REFUSAL, ["Psu-Ip-Address"])],
)
async def test_a_refused_background_window_is_cut_and_kept_for_later(
    eb_keys, refusal, required,
):
    since = TODAY - timedelta(days=200)
    windows, provider = await _fetch_since(since, required=required, refuse_before=refusal)
    assert windows == [(since, TODAY), (OLDEST_SERVED, TODAY)]
    # The sync keeps last_sync_at, so the cut range is asked for again.
    assert provider.incomplete_account_ids == {"acc-1"}


@pytest.mark.asyncio
async def test_a_window_refused_with_the_user_present_is_cut_for_good(eb_keys):
    """With the user's headers the refusal is final: only new SCA would help."""
    since = TODAY - timedelta(days=200)
    windows, provider = await _fetch_since(since, context=USER, refuse_before=WRONG_PERIOD)
    assert windows == [(since, TODAY), (OLDEST_SERVED, TODAY)]
    assert provider.incomplete_account_ids == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refusal", "error"),
    [(WRONG_PERIOD, httpx.HTTPStatusError), (PRESENCE_REFUSAL, ProviderUserActionRequired)],
)
async def test_a_refused_window_inside_90_days_is_not_retried(eb_keys, refusal, error):
    since = TODAY - timedelta(days=20)
    provider = EnableBankingProvider()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return refusal

    credentials = {"session_id": "s", "aspsp": {"name": "Bank", "required_psu_headers": ["Psu-Ip-Address"]}}
    with _patch_client(provider, handler), pytest.raises(error):
        await provider.get_transactions(credentials, "acc-1", since=since)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_another_error_on_a_long_window_is_not_retried(eb_keys):
    since = TODAY - timedelta(days=200)
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_since(since, refuse_before=httpx.Response(403, json={"error": "X"}))


# A user-present sync whose request had no public IP, for a bank that needs one:
# it sends no PSU headers, so the bank treats it as a background request.
NO_IP = PsuContext(headers={"Psu-User-Agent": "Firefox"})


@pytest.mark.asyncio
async def test_a_headerless_user_present_cut_is_kept_for_later(eb_keys):
    since = TODAY - timedelta(days=200)
    windows, provider = await _fetch_since(
        since, context=NO_IP, required=["Psu-Ip-Address"], refuse_before=PRESENCE_REFUSAL
    )
    assert windows[-1] == (OLDEST_SERVED, TODAY)
    assert provider.incomplete_account_ids == {"acc-1"}


@pytest.mark.asyncio
async def test_a_cut_window_keeps_last_sync_at(
    eb_keys, session: AsyncSession, test_user, test_workspace,
):
    """End to end: the background cut leaves last_sync_at where it was, so the
    next sync, with the user present, asks for the same full window."""
    last_sync = datetime.now(timezone.utc) - timedelta(days=200)
    conn = BankConnection(
        user_id=test_user.id, workspace_id=test_workspace.id, provider="enable_banking",
        external_id="sess-1", institution_name="Bank", status="active", last_sync_at=last_sync,
        credentials={
            "session_id": "sess-1",
            "aspsp": {"name": "Bank", "country": "NO", "required_psu_headers": ["Psu-Ip-Address"]},
            ACCOUNT_DETAILS_KEY: {"acc-1": {"name": "Brukskonto", "currency": "NOK"}},
        },
    )
    session.add(conn)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transactions"):
            if _window(request)[0] < OLDEST_SERVED:
                return PRESENCE_REFUSAL
            return httpx.Response(200, json={"transactions": {"booked": [], "pending": []}})
        return _bank([])(request)

    provider = EnableBankingProvider()
    with _patch_client(provider, handler), \
         patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        _, _, outcome = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert outcome == "refreshed"
    session.expunge_all()
    stored = await session.get(BankConnection, conn.id)
    assert stored is not None and stored.status == "active"
    assert stored.last_sync_at is not None
    assert stored.last_sync_at.replace(tzinfo=timezone.utc) == last_sync


@pytest.mark.asyncio
async def test_headerless_user_present_sync_reuses_cached_details(eb_keys):
    provider = EnableBankingProvider()
    calls: list[str] = []
    creds = _credentials({"acc-1": {"name": "Brukskonto", "currency": "NOK"}})
    creds["aspsp"]["required_psu_headers"] = ["Psu-Ip-Address"]
    with _patch_client(provider, _bank(calls)), psu_present(NO_IP):
        await provider.get_accounts(creds)

    assert "/accounts/acc-1/details" not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.Response(503), httpx.ReadTimeout("slow")])
async def test_cached_account_with_an_unavailable_balance_is_not_synced_as_zero(
    eb_keys, failure,
):
    """With the details cached the balance is the only call, so its failure
    skips the account for this run like a failed /details does."""
    provider = EnableBankingProvider()
    accounts = [{"uid": "acc-1"}, {"uid": "acc-2"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-1":
            return httpx.Response(200, json={"accounts_data": accounts})
        if request.url.path == "/accounts/acc-1/balances":
            if isinstance(failure, Exception):
                raise failure
            return failure
        return httpx.Response(200, json={"balances": [
            {"balance_type": "CLBD", "balance_amount": {"amount": "5.00", "currency": "NOK"}},
        ]})

    cache = {uid: {"name": f"Account {uid}", "currency": "NOK"} for uid in ("acc-1", "acc-2")}
    with _patch_client(provider, handler):
        result = await provider.get_accounts(_credentials(cache))

    assert [(a.external_id, a.balance) for a in result] == [("acc-2", Decimal("5.00"))]

    # When it was the only account, the failure itself is what the sync sees.
    accounts = [{"uid": "acc-1"}]
    with _patch_client(provider, handler), pytest.raises(httpx.HTTPError):
        await provider.get_accounts(_credentials(cache))


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
