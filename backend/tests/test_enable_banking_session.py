"""Consent failures must reach sync's existing reconnect/rollback handlers.

All payloads are synthetic; MockTransport prevents provider network access.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers.base import ProviderRateLimited, ProviderUserActionRequired, SessionExpiredError
from app.providers.enable_banking import EnableBankingProvider
from app.services.connection_service import sync_connection


@pytest.fixture
def eb_transport(monkeypatch):
    def make(*, status="AUTHORIZED", failure=None, accounts=None):
        provider = EnableBankingProvider()
        paths = []

        def handler(request):
            path = request.url.path
            paths.append(path)
            if path == "/sessions/test-session":
                return httpx.Response(200, json={
                    "status": status,
                    "accounts": ["test-account"] if accounts is None else accounts,
                })
            resource = path.rsplit("/", 1)[-1]
            if failure and resource == failure[0]:
                return httpx.Response(failure[1], json={"error": "ASPSP_ERROR"})
            if resource == "details":
                return httpx.Response(200, json={
                    "uid": "test-account", "name": "Test account", "currency": "EUR",
                })
            if resource == "balances":
                return httpx.Response(200, json={"balances": [{
                    "balance_type": "CLBD",
                    "balance_amount": {"amount": "123.45", "currency": "EUR"},
                }]})
            if resource == "transactions":
                return httpx.Response(200, json={"transactions": []})
            raise AssertionError("Unexpected mock request")

        monkeypatch.setattr(provider, "_client", lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://eb.test",
        ))
        return provider, paths

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "EXPIRED", "REVOKED", "CANCELLED", "CLOSED", "INVALID",
    "PENDING_AUTHORIZATION", "RETURNED_FROM_BANK", "UNKNOWN", None,
])
async def test_only_authorized_sessions_can_read_accounts(eb_transport, status):
    provider, paths = eb_transport(status=status)
    with pytest.raises(SessionExpiredError):
        await provider.get_accounts({"session_id": "test-session"})
    assert paths == ["/sessions/test-session"]


@pytest.mark.asyncio
async def test_authorized_session_reads_balance(eb_transport):
    provider, paths = eb_transport()
    accounts = await provider.get_accounts({"session_id": "test-session"})
    assert len(accounts) == 1
    assert accounts[0].balance == Decimal("123.45")
    assert len(paths) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["details", "balances", "transactions"])
@pytest.mark.parametrize("code", [401, 403, 410])
async def test_account_auth_errors_are_not_swallowed(eb_transport, resource, code):
    provider, _ = eb_transport(failure=(resource, code))
    with pytest.raises(SessionExpiredError):
        if resource == "transactions":
            await provider.get_transactions({"session_id": "test-session"}, "test-account")
        else:
            await provider.get_accounts({"session_id": "test-session"})


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["details", "balances"])
async def test_transient_failures_do_not_return_empty_or_zero(eb_transport, resource):
    provider, _ = eb_transport(failure=(resource, 503))
    with pytest.raises(RuntimeError):
        await provider.get_accounts({"session_id": "test-session"})


@pytest.mark.asyncio
async def test_empty_authorized_session_requires_portal_action(eb_transport):
    provider, _ = eb_transport(accounts=[])
    with pytest.raises(ProviderUserActionRequired) as exc:
        await provider.get_accounts({"session_id": "test-session"})
    assert exc.value.code == "no_accounts_linked"


@pytest.mark.asyncio
async def test_rate_limit_stays_distinct_from_expired_consent(eb_transport):
    provider, _ = eb_transport(failure=("balances", 429))
    with pytest.raises(ProviderRateLimited):
        await provider.get_accounts({"session_id": "test-session"})


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["details", "balances"])
async def test_partial_failure_preserves_readable_accounts(eb_transport, monkeypatch, resource):
    provider, _ = eb_transport(accounts=["unavailable", "test-account"])
    request = provider._request

    async def partial(method, path, **kwargs):
        if path == f"/accounts/unavailable/{resource}":
            raise httpx.ConnectError("Synthetic outage")
        if path == "/accounts/unavailable/details":
            return {"uid": "unavailable", "currency": "EUR"}
        return await request(method, path, **kwargs)

    monkeypatch.setattr(provider, "_request", partial)
    accounts = await provider.get_accounts({"session_id": "test-session"})
    assert [a.external_id for a in accounts] == ["test-account"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,failure,expected", [
    ("EXPIRED", None, "expired"),
    ("AUTHORIZED", ("details", 401), "expired"),
    ("AUTHORIZED", ("balances", 403), "expired"),
    ("AUTHORIZED", ("transactions", 401), "expired"),
    ("AUTHORIZED", ("details", 400), "error"),
    ("AUTHORIZED", ("balances", 503), "error"),
])
async def test_sync_failure_preserves_timestamp_and_balance(
    session, test_user, test_workspace, test_connection, test_account,
    eb_transport, status, failure, expected,
):
    provider, _ = eb_transport(status=status, failure=failure)
    test_connection.provider = "enable_banking"
    test_connection.workspace_id = test_workspace.id
    test_connection.credentials = {
        "session_id": "test-session",
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }
    test_connection.logo_url = "https://example.test/logo.png"
    test_account.external_id = "test-account"
    test_account.workspace_id = test_workspace.id
    test_account.currency = "EUR"
    await session.commit()
    conn_id, user_id, workspace_id = test_connection.id, test_user.id, test_workspace.id
    previous_sync = test_connection.last_sync_at
    previous_balance = test_account.balance
    error = SessionExpiredError if expected == "expired" else RuntimeError
    with patch("app.services.connection_service.get_provider", return_value=provider):
        with pytest.raises(error):
            await sync_connection(session, conn_id, workspace_id, user_id)

    await session.refresh(test_connection)
    await session.refresh(test_account)
    assert test_connection.status == expected
    # SQLite drops timezone information on reload.
    assert test_connection.last_sync_at.replace(tzinfo=None) == previous_sync.replace(tzinfo=None)
    assert test_account.balance == previous_balance


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 429, 503])
async def test_oauth_balance_failure_is_not_a_fake_zero(eb_transport, monkeypatch, code):
    provider, _ = eb_transport(failure=("balances", code))
    original_request = provider._request

    async def request(method, path, **kwargs):
        if method == "POST" and path == "/sessions":
            return {"session_id": "test-session", "accounts": [{"uid": "test-account"}]}
        return await original_request(method, path, **kwargs)

    monkeypatch.setattr(provider, "_request", request)
    expected = ProviderRateLimited if code == 429 else (
        RuntimeError if code == 503 else SessionExpiredError
    )
    with pytest.raises(expected):
        await provider.handle_oauth_callback("synthetic-code")


@pytest.mark.asyncio
async def test_oauth_partial_balance_failure_keeps_healthy_account(eb_transport, monkeypatch):
    provider, _ = eb_transport()
    original_request = provider._request

    async def request(method, path, **kwargs):
        if method == "POST" and path == "/sessions":
            return {"session_id": "test-session", "accounts": [
                {"uid": "unavailable"}, {"uid": "test-account"},
            ]}
        if path == "/accounts/unavailable/balances":
            raise httpx.ConnectError("Synthetic outage")
        return await original_request(method, path, **kwargs)

    monkeypatch.setattr(provider, "_request", request)
    connection = await provider.handle_oauth_callback("synthetic-code")
    assert [account.external_id for account in connection.accounts] == ["test-account"]
