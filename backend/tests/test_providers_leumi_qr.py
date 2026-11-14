"""Bank Leumi QR provider, with the leumi-qr sidecar faked by httpx.MockTransport.

All account numbers and amounts here are made up.
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
from app.providers.leumi_qr import LeumiQrProvider

SESSION = "11111111-2222-4333-8444-555555555555"

RESULT = {
    "scraped_at": "2026-03-15T10:00:00Z",
    "accounts": [
        {
            "accountNumber": "100-123456_78",
            "currency": "ILS",
            "balance": 1000.5,
            "savingsAccount": False,
            "txns": [
                {"date": "2026-03-01", "amount": -100, "currency": "ILS", "description": "Cheque",
                 "memo": "", "identifier": "R1", "status": "posted"},
                {"date": "2026-03-01", "amount": 5000, "currency": "ILS", "description": "Salary",
                 "memo": "transfer from Example Ltd", "identifier": "R2", "status": "posted"},
                # Leumi appends this standard explanation to channel-fee rows.
                {"date": "2026-03-02", "amount": -10, "currency": "ILS", "description": "Channel fee",
                 "memo": "סכום החיוב מייצג את מספר התנועות", "identifier": "R3", "status": "posted"},
                # Two genuinely identical rows on the same day.
                {"date": "2026-03-03", "amount": -5, "currency": "ILS", "description": "Fee",
                 "memo": "", "identifier": "R4", "status": "posted"},
                {"date": "2026-03-03", "amount": -5, "currency": "ILS", "description": "Fee",
                 "memo": "", "identifier": "R4", "status": "posted"},
                {"date": "2026-03-14", "amount": -20, "currency": "ILS", "description": "Today",
                 "memo": "", "identifier": "R5", "status": "pending"},
            ],
        },
        {"accountNumber": "123456/78 001 USD", "currency": "USD", "balance": 300,
         "savingsAccount": False,
         "txns": [{"date": "2026-03-06", "amount": -150, "currency": "USD",
                   "description": "Securities purchase", "memo": "", "identifier": "R6", "status": "posted"}]},
        {"accountNumber": "12345678-99", "balance": 10000, "savingsAccount": True, "txns": []},
    ],
}


def _patched(handler):
    transport = httpx.MockTransport(handler)

    def fake_client(self):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, base_url="http://leumi-qr:8095", timeout=5)

    return patch.object(LeumiQrProvider, "_client", fake_client)


def _sidecar(result_status: int = 200, sessions_status: int = 201, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "POST" and request.url.path == "/sessions":
            if sessions_status != 201:
                return httpx.Response(sessions_status, json={"error": "busy"})
            return httpx.Response(201, json={"id": SESSION})
        if request.url.path == f"/sessions/{SESSION}/result":
            # The service hands a result only to the user who started the session.
            if request.url.params.get("user_id") != "user-1":
                return httpx.Response(404, json={"error": "not found"})
            if result_status != 200:
                return httpx.Response(result_status, json={"error": "gone"})
            return httpx.Response(200, json=RESULT)
        if request.url.path == f"/sessions/{SESSION}":
            if request.url.params.get("user_id") != "user-1":
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"status": "waiting_for_scan", "qr": "iVBOR", "error": None})
        return httpx.Response(404)

    return handler


def test_known_provider_advertises_qr_flow():
    leumi = next(p for p in KNOWN_PROVIDERS if p["name"] == "leumi")
    assert leumi["flow_type"] == "qr"
    assert leumi["supports_asset_sync"] is False


@pytest.mark.asyncio
async def test_connect_token_starts_a_session_for_the_user():
    seen: list[httpx.Request] = []
    with _patched(_sidecar(seen=seen)):
        token = await LeumiQrProvider().create_connect_token("user-1")
    assert token.access_token == SESSION
    body = json.loads(seen[0].content)
    assert body["user_id"] == "user-1"
    assert date.fromisoformat(body["start_date"]) < date.today()


@pytest.mark.asyncio
async def test_connect_token_when_another_login_is_running():
    with _patched(_sidecar(sessions_status=409)):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await LeumiQrProvider().create_connect_token("user-1")
    assert exc.value.code == "qr_busy"


@pytest.mark.asyncio
async def test_qr_session_is_only_visible_to_its_user():
    with _patched(_sidecar()):
        state = await LeumiQrProvider().get_qr_session(SESSION, "user-1")
        assert state == {"status": "waiting_for_scan", "qr": "iVBOR", "error": None}
        with pytest.raises(SessionExpiredError):
            await LeumiQrProvider().get_qr_session(SESSION, "someone-else")


@pytest.mark.asyncio
async def test_callback_builds_accounts_without_storing_full_numbers():
    with _patched(_sidecar()):
        conn = await LeumiQrProvider().complete_qr_session(SESSION, "user-1")
    assert conn.institution_name == "Bank Leumi"
    assert conn.credentials["session_id"] == SESSION
    assert conn.credentials["user_id"] == "user-1"
    by_currency = {a.currency: a for a in conn.accounts if a.type == "checking"}
    assert by_currency["ILS"].balance == Decimal("1000.5")
    assert by_currency["ILS"].masked_number == "5678"
    # The FX account number carries its currency; only digits are masked.
    assert by_currency["USD"].masked_number == "8001"
    assert by_currency["USD"].name == "Leumi USD ****8001"
    assert by_currency["ILS"].external_id != by_currency["USD"].external_id
    savings = [a for a in conn.accounts if a.type == "savings"]
    assert len(savings) == 1 and savings[0].name.startswith("Leumi deposit")
    for account in conn.accounts:
        assert "123456" not in account.external_id


@pytest.mark.asyncio
async def test_reconnect_keeps_the_same_connection_identity():
    with _patched(_sidecar()):
        first = await LeumiQrProvider().complete_qr_session(SESSION, "user-1")
        again = await LeumiQrProvider().complete_qr_session(SESSION, "user-1")
    assert first.external_id == again.external_id
    assert [a.external_id for a in first.accounts] == [a.external_id for a in again.accounts]


@pytest.mark.asyncio
async def test_another_users_session_cannot_be_imported():
    with _patched(_sidecar()):
        with pytest.raises(SessionExpiredError):
            await LeumiQrProvider().complete_qr_session(SESSION, "user-2")


@pytest.mark.asyncio
async def test_qr_provider_has_no_plain_oauth_callback():
    with pytest.raises(NotImplementedError):
        await LeumiQrProvider().handle_oauth_callback(SESSION)


def test_transactions_skip_pending_and_sign_amounts():
    txns = LeumiQrProvider.build_transactions(RESULT["accounts"][0])
    assert all(t.status == "posted" for t in txns)
    assert [t.description for t in txns if t.date == date(2026, 3, 14)] == []
    cheque = next(t for t in txns if t.description == "Cheque")
    assert (cheque.amount, cheque.type) == (Decimal("100"), "debit")
    salary = next(t for t in txns if t.description.startswith("Salary"))
    assert salary.type == "credit"
    assert salary.description == "Salary | transfer from Example Ltd"
    fee = next(t for t in txns if t.description.startswith("Channel fee"))
    assert fee.description == "Channel fee"  # boilerplate memo dropped


def test_identical_rows_get_distinct_stable_ids():
    first = LeumiQrProvider.build_transactions(RESULT["accounts"][0])
    second = LeumiQrProvider.build_transactions(RESULT["accounts"][0])
    ids = [t.external_id for t in first]
    assert len(ids) == len(set(ids))
    assert ids == [t.external_id for t in second]


def test_since_filters_older_rows():
    txns = LeumiQrProvider.build_transactions(RESULT["accounts"][0], since=date(2026, 3, 2))
    assert min(t.date for t in txns) == date(2026, 3, 2)


@pytest.mark.asyncio
async def test_transactions_are_read_per_account_and_currency():
    provider = LeumiQrProvider()
    with _patched(_sidecar()):
        accounts = await provider.get_accounts({"session_id": SESSION, "user_id": "user-1"})
        usd = next(a for a in accounts if a.currency == "USD")
        txns = await provider.get_transactions({"session_id": SESSION, "user_id": "user-1"}, usd.external_id)
    assert len(txns) == 1
    assert txns[0].currency == "USD" and txns[0].amount == Decimal("150")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 409])
async def test_expired_or_unfinished_session_asks_for_a_new_scan(status_code):
    provider = LeumiQrProvider()
    with _patched(_sidecar(result_status=status_code)):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"session_id": SESSION, "user_id": "user-1"})
        assert await provider.trigger_refresh({"session_id": SESSION, "user_id": "user-1"}) == "needs_user_action"


@pytest.mark.asyncio
async def test_live_session_skips_refresh():
    with _patched(_sidecar()):
        assert await LeumiQrProvider().trigger_refresh({"session_id": SESSION, "user_id": "user-1"}) == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [{}, {"session_id": SESSION}])
async def test_missing_session_or_user_is_expired(credentials):
    # Connections made before sessions were bound to their user need a new scan.
    with pytest.raises(SessionExpiredError):
        await LeumiQrProvider().get_accounts(credentials)


# ----- API: polling a QR session ------------------------------------------------


@pytest.mark.asyncio
async def test_qr_session_endpoint_returns_state(client, auth_headers):
    from unittest.mock import AsyncMock

    state = {"status": "waiting_for_scan", "qr": "iVBOR", "error": None}
    with patch("app.services.connection_service.get_qr_session", AsyncMock(return_value=state)) as m:
        resp = await client.get(f"/api/connections/leumi/qr-sessions/{SESSION}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == state
    assert str(m.await_args.args[1]) == SESSION


@pytest.mark.asyncio
async def test_qr_session_endpoint_expired(client, auth_headers):
    from unittest.mock import AsyncMock

    expired = AsyncMock(side_effect=SessionExpiredError("gone"))
    with patch("app.services.connection_service.get_qr_session", expired):
        resp = await client.get(f"/api/connections/leumi/qr-sessions/{SESSION}", headers=auth_headers)
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_qr_session_endpoint_rejects_malformed_ids(client, auth_headers):
    resp = await client.get("/api/connections/leumi/qr-sessions/not-a-uuid", headers=auth_headers)
    assert resp.status_code == 422
