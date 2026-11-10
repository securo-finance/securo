"""PSU headers: the user's own on user-present Enable Banking requests, none otherwise."""
import json
from unittest.mock import patch

import httpx
import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.core.config import Settings
from app.models.bank_connection import BankConnection
from app.providers.base import ProviderRateLimited, ProviderUserActionRequired
from app.providers.enable_banking import EnableBankingProvider, _select_psu_headers
from app.providers.psu import (
    PsuContext,
    current_psu_context,
    psu_context_from_request,
    psu_present,
)

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
USER = PsuContext(
    headers={
        "Psu-Ip-Address": "1.1.1.1",
        "Psu-User-Agent": BROWSER_UA,
        "Psu-Accept-Language": "nb-NO,nb;q=0.9",
    }
)


@pytest.fixture
def eb_keys(monkeypatch):
    monkeypatch.setattr(EnableBankingProvider, "_jwt_token", classmethod(lambda cls: "jwt"))


def _patch_client(provider: EnableBankingProvider, handler):
    transport = httpx.MockTransport(handler)

    def fake_client():
        return httpx.AsyncClient(base_url="https://api.enablebanking.com", transport=transport)

    return patch.object(provider, "_client", side_effect=fake_client)


def _psu_headers(request: httpx.Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items() if k.lower().startswith("psu-")}


# ----- which headers -----


def test_sends_ip_and_user_agent_when_nothing_is_required():
    assert _select_psu_headers(USER.headers, None) == {
        "Psu-Ip-Address": "1.1.1.1",
        "Psu-User-Agent": BROWSER_UA,
    }
    assert _select_psu_headers(USER.headers, []) == _select_psu_headers(USER.headers, None)


def test_adds_required_headers_matched_case_insensitively():
    selected = _select_psu_headers(USER.headers, ["psu-ip-address", "Psu-Accept-Language"])
    assert selected == {
        "Psu-Ip-Address": "1.1.1.1",
        "Psu-User-Agent": BROWSER_UA,
        "Psu-Accept-Language": "nb-NO,nb;q=0.9",
    }


def test_sends_none_when_a_required_header_is_missing():
    """EB: all of an ASPSP's required PSU headers, or none of them."""
    no_ip = {"Psu-User-Agent": BROWSER_UA}
    assert _select_psu_headers(no_ip, ["Psu-Ip-Address"]) == {}
    assert _select_psu_headers(USER.headers, ["Psu-Ip-Address", "Psu-Geo-Location"]) == {}


# ----- what the user's request supplies -----


def _request(client_host: str, headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/connections/x/sync",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 51000),
    })


def _context(client_host, headers, hops=0):
    settings = Settings(trusted_proxy_hops=hops, _env_file=None, _secrets_dir=None)
    with patch("app.providers.psu.get_settings", return_value=settings):
        return psu_context_from_request(_request(client_host, headers)).headers


def test_request_context_copies_the_users_headers():
    headers = _context("1.1.1.1", {
        "User-Agent": BROWSER_UA,
        "Accept-Language": "nb-NO",
        "Referer": "https://securo.example/accounts",
        "Cookie": "session=secret",
    })
    assert headers == {
        "Psu-Ip-Address": "1.1.1.1",
        "Psu-User-Agent": BROWSER_UA,
        "Psu-Accept-Language": "nb-NO",
        "Psu-Referer": "https://securo.example/accounts",
    }


def test_request_context_drops_headers_httpx_cannot_send():
    """Starlette reads header bytes as latin-1; httpx only sends ASCII strings."""
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/connections/x/sync",
        "headers": [
            (b"user-agent", "Navigateur/1.0 (é)".encode("latin-1")),
            (b"accept-language", b"nb-NO"),
        ],
        "client": ("1.1.1.1", 51000),
    })
    settings = Settings(trusted_proxy_hops=0, _env_file=None, _secrets_dir=None)
    with patch("app.providers.psu.get_settings", return_value=settings):
        headers = psu_context_from_request(request).headers
    assert headers == {"Psu-Ip-Address": "1.1.1.1", "Psu-Accept-Language": "nb-NO"}
    # What is left is something httpx can put on the wire.
    httpx.Request("GET", "https://example.test", headers=headers)


@pytest.mark.parametrize("host", ["172.18.0.5", "10.0.0.2", "127.0.0.1", "::1", "testclient"])
def test_request_context_drops_a_non_public_peer_address(host):
    """Behind the bundled nginx the peer is the proxy, not the user."""
    assert "Psu-Ip-Address" not in _context(host, {"User-Agent": BROWSER_UA})


def test_request_context_ignores_forwarded_for_without_trusted_proxies():
    headers = _context("172.18.0.5", {"X-Forwarded-For": "1.1.1.1"})
    assert "Psu-Ip-Address" not in headers


def test_request_context_reads_forwarded_for_only_as_far_as_trusted():
    # The client wrote 9.9.9.9 itself; the one trusted proxy appended 1.1.1.1.
    headers = _context("172.18.0.5", {"X-Forwarded-For": "9.9.9.9, 1.1.1.1"}, hops=1)
    assert headers["Psu-Ip-Address"] == "1.1.1.1"


def test_no_context_outside_a_request():
    assert current_psu_context() is None
    with psu_present(USER):
        assert current_psu_context() is USER
    assert current_psu_context() is None


# ----- what the provider sends -----


@pytest.mark.asyncio
async def test_user_present_data_requests_carry_psu_headers(eb_keys):
    provider = EnableBankingProvider()
    seen: dict[str, dict[str, str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = _psu_headers(request)
        if request.url.path == "/sessions/sess-1":
            return httpx.Response(200, json={"accounts_data": [{"uid": "acc-1"}]})
        if request.url.path == "/accounts/acc-1/details":
            return httpx.Response(200, json={"uid": "acc-1", "currency": "NOK"})
        return httpx.Response(200, json={"balances": []})

    credentials = {
        "session_id": "sess-1",
        "aspsp": {"name": "Bank", "country": "NO", "required_psu_headers": ["Psu-Ip-Address"]},
    }
    with _patch_client(provider, handler), psu_present(USER):
        await provider.get_accounts(credentials)

    expected = {"psu-ip-address": "1.1.1.1", "psu-user-agent": BROWSER_UA}
    assert seen["/accounts/acc-1/details"] == expected
    assert seen["/accounts/acc-1/balances"] == expected
    # The session lookup is answered by Enable Banking, not the bank.
    assert seen["/sessions/sess-1"] == {}


@pytest.mark.asyncio
async def test_user_present_transaction_requests_carry_psu_headers(eb_keys):
    provider = EnableBankingProvider()
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounts/acc-1/transactions"
        seen.append(_psu_headers(request))
        return httpx.Response(200, json={"transactions": {"booked": [], "pending": []}})

    credentials = {
        "session_id": "sess-1",
        "aspsp": {"name": "Bank", "required_psu_headers": ["Psu-Accept-Language"]},
    }
    with _patch_client(provider, handler), psu_present(USER):
        await provider.get_transactions(credentials, "acc-1")

    assert seen == [{
        "psu-ip-address": "1.1.1.1",
        "psu-user-agent": BROWSER_UA,
        "psu-accept-language": "nb-NO,nb;q=0.9",
    }]


@pytest.mark.asyncio
async def test_background_requests_carry_no_psu_headers(eb_keys):
    provider = EnableBankingProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"transactions": {"booked": [], "pending": []}})

    with _patch_client(provider, handler):
        await provider.get_transactions({"session_id": "s"}, "acc-1")

    assert requests
    assert all(_psu_headers(r) == {} for r in requests)


ASPSP_ERROR = {"code": 400, "message": "Error interacting with ASPSP", "error": "ASPSP_ERROR"}
# The body reported in issue #1239 for a bank that wants Psu-Ip-Address.
MISSING_HEADER_429 = {
    "code": 429,
    "message": "HTTP header is required: PSU-IP-Address.",
    "error": "ASPSP_RATE_LIMIT_EXCEEDED",
}
PLAIN_429 = {"code": 429, "message": "ASPSP Rate limit exceeded", "error": "ASPSP_RATE_LIMIT_EXCEEDED"}


async def _fetch_balance(required, status, body, context=None):
    provider = EnableBankingProvider()
    provider._use_credentials({"aspsp": {"name": "Bank", "required_psu_headers": required}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(body).encode())

    with _patch_client(provider, handler), psu_present(context):
        return await provider._request("GET", "/accounts/acc-1/balances")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, ASPSP_ERROR),
        (403, {"code": 403, "message": "HTTP header is required: PSU-IP-Address."}),
        (422, {"code": 422, "message": "x", "error": "PSU_HEADER_NOT_PROVIDED"}),
    ],
)
async def test_background_refusal_asks_for_a_present_user(eb_keys, status, body):
    """A bank that wants the user present gets the user, never invented headers."""
    with pytest.raises(ProviderUserActionRequired) as exc:
        await _fetch_balance(["Psu-Ip-Address"], status, body)
    assert exc.value.code == "psu_present_required"


@pytest.mark.asyncio
async def test_refusal_is_a_plain_error_when_the_bank_requires_no_headers(eb_keys):
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_balance([], 400, ASPSP_ERROR)


@pytest.mark.asyncio
async def test_refusal_with_the_users_headers_is_a_plain_error(eb_keys):
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_balance(["Psu-Ip-Address"], 400, ASPSP_ERROR, context=USER)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [PLAIN_429, MISSING_HEADER_429])
@pytest.mark.parametrize("required", [["Psu-Ip-Address"], None])
async def test_a_429_stays_a_rate_limit_whatever_its_body(eb_keys, body, required):
    """A 429 that names the PSU header is still the bank's daily allowance
    running out: it resets on its own, so the connection is not errored."""
    with pytest.raises(ProviderRateLimited):
        await _fetch_balance(required, 429, body)


MISSING_HEADER_400 = {
    **ASPSP_ERROR,
    "detail": {"message": "FORMAT_ERROR: Missing PSU-IP-Address header"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, MISSING_HEADER_400),
        (403, {"code": 403, "message": "HTTP header is required: PSU-IP-Address."}),
    ],
)
async def test_a_refusal_naming_the_header_counts_without_aspsp_metadata(eb_keys, status, body):
    """/aspsps can fail or omit the field; the bank's own words still count."""
    with pytest.raises(ProviderUserActionRequired) as exc:
        await _fetch_balance(None, status, body)
    assert exc.value.code == "psu_present_required"


@pytest.mark.asyncio
async def test_a_generic_aspsp_error_without_metadata_is_a_plain_error(eb_keys):
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_balance(None, 400, ASPSP_ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {**ASPSP_ERROR, "detail": {"message": "Service temporarily unavailable"}},
        {**ASPSP_ERROR, "message": "Service temporarily unavailable"},
    ],
)
async def test_an_aspsp_error_with_another_reason_is_a_plain_error(eb_keys, body):
    """A bank that wants headers can still fail for something else."""
    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_balance(["Psu-Ip-Address"], 400, body)


# ----- where the required headers come from -----


def _aspsps_handler(calls: list[str], required):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/aspsps":
            entry = {"name": "Bank", "country": "NO"}
            if required is not None:
                entry["required_psu_headers"] = required
            return httpx.Response(200, json={"aspsps": [{"name": "Other"}, entry]})
        return httpx.Response(500)

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(("listed", "stored"), [(["Psu-Ip-Address"], ["Psu-Ip-Address"]), (None, [])])
async def test_refresh_credentials_backfills_required_headers(eb_keys, listed, stored):
    provider = EnableBankingProvider()
    calls: list[str] = []
    creds = {"session_id_enc": "enc", "aspsp": {"name": "Bank", "country": "NO"}}
    with _patch_client(provider, _aspsps_handler(calls, listed)):
        out = await provider.refresh_credentials(creds)
        # Stored now, so the next sync does not ask again.
        again = await provider.refresh_credentials(out)

    assert out["aspsp"]["required_psu_headers"] == stored
    assert out["session_id_enc"] == "enc"
    assert again is out
    assert calls == ["/aspsps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [123, "Psu-Ip-Address", {"Psu-Ip-Address": True}])
async def test_malformed_required_headers_are_unknown(eb_keys, malformed):
    provider = EnableBankingProvider()
    calls: list[str] = []
    creds = {"session_id_enc": "enc", "aspsp": {"name": "Bank", "country": "NO"}}
    with _patch_client(provider, _aspsps_handler(calls, malformed)):
        assert await provider._lookup_required_psu_headers("Bank", "NO") is None
        # Unknown is not stored, so a later sync asks again.
        assert await provider.refresh_credentials(creds) is creds


@pytest.mark.asyncio
async def test_refresh_credentials_keeps_credentials_when_lookup_fails(eb_keys):
    provider = EnableBankingProvider()
    creds = {"aspsp": {"name": "Bank", "country": "NO"}}
    with _patch_client(provider, lambda request: httpx.Response(503)):
        assert await provider.refresh_credentials(creds) is creds


@pytest.mark.asyncio
async def test_oauth_callback_stores_required_headers_and_sends_the_users(eb_keys):
    provider = EnableBankingProvider()
    balance_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={
                "session_id": "sess-2",
                "accounts": [{"uid": "acc-1", "currency": "NOK", "display_name": "Main"}],
                "aspsp": {"name": "Bank", "country": "NO"},
                "access": {"valid_until": "2027-01-01T00:00:00Z"},
            })
        if request.url.path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "Bank", "country": "NO", "required_psu_headers": ["psu-ip-address"]},
            ]})
        balance_headers.update(_psu_headers(request))
        return httpx.Response(200, json={"balances": []})

    with _patch_client(provider, handler), psu_present(USER):
        conn = await provider.handle_oauth_callback("code")

    assert conn.credentials["aspsp"]["required_psu_headers"] == ["psu-ip-address"]
    assert balance_headers == {"psu-ip-address": "1.1.1.1", "psu-user-agent": BROWSER_UA}


@pytest.mark.asyncio
async def test_oauth_callback_keeps_the_session_when_balances_need_a_missing_ip(eb_keys):
    """The authorization code is single-use: a refused balance must not lose it."""
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={
                "session_id": "sess-2",
                "accounts": [{"uid": "acc-1", "currency": "NOK", "display_name": "Main"}],
                "aspsp": {"name": "Bank", "country": "NO"},
                "access": {"valid_until": "2027-01-01T00:00:00Z"},
            })
        if request.url.path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "Bank", "country": "NO", "required_psu_headers": ["Psu-Ip-Address"]},
            ]})
        return httpx.Response(400, json=ASPSP_ERROR)

    no_ip = PsuContext(headers={"Psu-User-Agent": BROWSER_UA})
    with _patch_client(provider, handler), psu_present(no_ip):
        conn = await provider.handle_oauth_callback("code")

    assert conn.external_id == "sess-2"
    assert conn.credentials["session_id_enc"]
    assert [a.external_id for a in conn.accounts] == ["acc-1"]


@pytest.mark.asyncio
async def test_sync_still_reports_a_balance_that_needs_the_user(eb_keys):
    """Outside the callback the refusal stays visible, not a zero balance."""
    provider = EnableBankingProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/sess-1":
            return httpx.Response(200, json={"accounts_data": [{"uid": "acc-1"}]})
        if request.url.path == "/accounts/acc-1/details":
            return httpx.Response(200, json={"uid": "acc-1", "currency": "NOK"})
        return httpx.Response(400, json=ASPSP_ERROR)

    credentials = {
        "session_id": "sess-1",
        "aspsp": {"name": "Bank", "required_psu_headers": ["Psu-Ip-Address"]},
    }
    with _patch_client(provider, handler), pytest.raises(ProviderUserActionRequired):
        await provider.get_accounts(credentials)


def _two_accounts_one_refused(refused: set[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sessions/sess-1":
            return httpx.Response(200, json={"accounts_data": [{"uid": "acc-1"}, {"uid": "acc-2"}]})
        uid = path.split("/")[2]
        if uid in refused:
            return httpx.Response(400, json=ASPSP_ERROR)
        if path.endswith("/details"):
            return httpx.Response(200, json={"uid": uid, "currency": "NOK", "name": uid})
        return httpx.Response(200, json={"balances": [
            {"balance_type": "CLBD", "balance_amount": {"amount": "5.00", "currency": "NOK"}},
        ]})

    return handler


REQUIRES_IP = {"name": "Bank", "required_psu_headers": ["Psu-Ip-Address"]}


@pytest.mark.asyncio
async def test_a_presence_refusal_skips_only_that_account(eb_keys):
    """Like a refused /details: the other accounts still sync."""
    provider = EnableBankingProvider()
    credentials = {"session_id": "sess-1", "aspsp": REQUIRES_IP}
    with _patch_client(provider, _two_accounts_one_refused({"acc-1"})):
        result = await provider.get_accounts(credentials)

    assert [a.external_id for a in result] == ["acc-2"]


@pytest.mark.asyncio
async def test_a_presence_refusal_of_every_account_fails_the_sync(eb_keys):
    provider = EnableBankingProvider()
    credentials = {"session_id": "sess-1", "aspsp": REQUIRES_IP}
    with _patch_client(provider, _two_accounts_one_refused({"acc-1", "acc-2"})), \
            pytest.raises(ProviderUserActionRequired) as exc:
        await provider.get_accounts(credentials)

    assert exc.value.code == "psu_present_required"


@pytest.mark.asyncio
async def test_the_proxy_hint_goes_to_the_log_not_to_the_user(eb_keys, caplog):
    """A member cannot act on TRUSTED_PROXY_HOPS; whoever runs the server can."""
    no_ip = PsuContext(headers={"Psu-User-Agent": BROWSER_UA})
    with caplog.at_level("WARNING"), pytest.raises(ProviderUserActionRequired) as exc:
        await _fetch_balance(["Psu-Ip-Address"], 400, ASPSP_ERROR, context=no_ip)

    assert "TRUSTED_PROXY_HOPS" not in str(exc.value)
    assert "TRUSTED_PROXY_HOPS" in caplog.text


@pytest.mark.asyncio
async def test_a_background_refusal_logs_no_proxy_hint(eb_keys, caplog):
    with caplog.at_level("WARNING"), pytest.raises(ProviderUserActionRequired):
        await _fetch_balance(["Psu-Ip-Address"], 400, ASPSP_ERROR)

    assert "TRUSTED_PROXY_HOPS" not in caplog.text


# ----- the API opens the context, the scheduler does not -----


@pytest.mark.asyncio
async def test_manual_sync_runs_with_the_users_psu_context(
    client: AsyncClient, auth_headers, test_connection: BankConnection,
):
    seen: list = []

    async def fake_sync(*args, **kwargs):
        seen.append(current_psu_context())
        return test_connection, 0, "refreshed"

    settings = Settings(trusted_proxy_hops=1, _env_file=None, _secrets_dir=None)
    with patch("app.services.connection_service.sync_connection", side_effect=fake_sync), \
         patch("app.providers.psu.get_settings", return_value=settings):
        resp = await client.post(
            f"/api/connections/{test_connection.id}/sync",
            headers={**auth_headers, "User-Agent": BROWSER_UA, "X-Forwarded-For": "1.1.1.1"},
        )

    assert resp.status_code == 200
    assert seen[0].headers["Psu-Ip-Address"] == "1.1.1.1"
    assert seen[0].headers["Psu-User-Agent"] == BROWSER_UA


@pytest.mark.asyncio
async def test_oauth_callback_runs_with_the_users_psu_context(
    client: AsyncClient, auth_headers, test_connection: BankConnection,
):
    seen: list = []

    async def fake_callback(*args, **kwargs):
        seen.append(current_psu_context())
        return test_connection

    with patch("app.services.connection_service.handle_oauth_callback", side_effect=fake_callback):
        resp = await client.post(
            "/api/connections/oauth/callback",
            json={"code": "c", "provider": "enable_banking", "state": "s"},
            headers={**auth_headers, "User-Agent": BROWSER_UA},
        )

    assert resp.status_code == 200, resp.text
    assert seen[0].headers["Psu-User-Agent"] == BROWSER_UA


@pytest.mark.asyncio
async def test_scheduled_sync_has_no_psu_context(session, test_user, test_workspace):
    from app.tasks.sync_tasks import _sync_one

    conn = BankConnection(
        user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="bg", institution_name="Bank", credentials={"t": 1}, status="active",
    )
    session.add(conn)
    await session.commit()
    seen: list = []

    async def fake_sync(*args, **kwargs):
        seen.append(current_psu_context())
        return conn, 0, "refreshed"

    with patch("app.tasks.sync_tasks.connection_service.sync_connection", side_effect=fake_sync):
        await _sync_one(lambda: session, conn.id, test_user.id)

    assert seen == [None]
