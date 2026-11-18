"""Why a connection's last sync failed, kept on the connection (issue #1405).

A failed sync leaves a stable code (psu_present_required, consent_expired,
rate_limited, provider_error), the time and a detail with no provider text,
for every provider and for manual and scheduled syncs alike. The next sync
that reads data clears it, and so does a reconnect.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import LAST_SYNC_FAILURE_KEY, BankConnection
from app.providers.base import (
    ProviderRateLimited,
    ProviderUserActionRequired,
    SessionExpiredError,
)
from app.schemas.bank_connection import BankConnectionRead
from app.services.connection_service import sync_connection
from tests.test_sync_rate_limit import (
    _make_connection,
    _mock_provider_two_accounts,
    _sync_patches,
)


def _failing_provider(exc: BaseException) -> AsyncMock:
    provider = _mock_provider_two_accounts()
    provider.get_accounts = AsyncMock(side_effect=exc)
    return provider


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://bank.example/accounts/IBAN-123/transactions")
    response = httpx.Response(status_code, request=request, text="account IBAN-123 of Jane Doe")
    return httpx.HTTPStatusError(
        "Jane Doe's account IBAN-123 failed", request=request, response=response
    )


async def _failed_sync(session, conn, workspace_id, user_id, exc) -> BankConnection:
    conn_id = conn.id
    with _sync_patches(_failing_provider(exc)):
        try:
            await sync_connection(session, conn_id, workspace_id, user_id)
        except Exception:
            pass
    session.expire_all()
    refreshed = await session.get(BankConnection, conn_id)
    assert refreshed is not None
    return refreshed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "code", "detail"),
    [
        (
            ProviderUserActionRequired("bank wants the user", code="psu_present_required"),
            "psu_present_required",
            "psu_present_required",
        ),
        (SessionExpiredError("consent expired"), "consent_expired", None),
        (
            ProviderUserActionRequired("token revoked", code="credentials_invalid"),
            "consent_expired",
            "credentials_invalid",
        ),
        (
            ProviderUserActionRequired("all refused", code="accounts_unavailable"),
            "provider_error",
            "accounts_unavailable",
        ),
        (ProviderRateLimited("429", retry_after=60), "rate_limited", None),
        (_http_error(502), "provider_error", "HTTP 502"),
        (RuntimeError("Jane Doe IBAN-123"), "provider_error", "RuntimeError"),
    ],
    ids=["psu", "expired", "credentials", "other-action", "429", "http", "crash"],
)
async def test_a_failed_sync_keeps_why_on_the_connection(
    session: AsyncSession, test_user, test_workspace, exc, code, detail
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    before = datetime.now(timezone.utc)

    conn = await _failed_sync(session, conn, test_workspace.id, test_user.id, exc)

    failure = BankConnectionRead.model_validate(conn).last_sync_failure
    assert failure is not None
    assert failure.code == code
    assert failure.detail == detail
    assert failure.at >= before
    # Nothing the provider wrote reaches the stored failure.
    assert "Jane" not in str(conn.settings) and "IBAN" not in str(conn.settings)


@pytest.mark.asyncio
async def test_the_next_sync_that_reads_data_clears_the_failure(
    session: AsyncSession, test_user, test_workspace
):
    workspace_id, user_id = test_workspace.id, test_user.id
    conn = await _make_connection(session, user_id, workspace_id)
    conn = await _failed_sync(session, conn, workspace_id, user_id, _http_error(500))
    assert conn.last_sync_failure is not None

    with _sync_patches(_mock_provider_two_accounts()):
        conn, _, outcome = await sync_connection(session, conn.id, workspace_id, user_id)

    assert outcome == "refreshed"
    assert conn.status == "active"
    assert LAST_SYNC_FAILURE_KEY not in (conn.settings or {})
    assert BankConnectionRead.model_validate(conn).last_sync_failure is None


@pytest.mark.asyncio
async def test_a_partial_sync_clears_the_failure(
    session: AsyncSession, test_user, test_workspace
):
    workspace_id, user_id = test_workspace.id, test_user.id
    conn = await _make_connection(session, user_id, workspace_id)
    conn = await _failed_sync(session, conn, workspace_id, user_id, SessionExpiredError("x"))

    provider = _mock_provider_two_accounts(rate_limit_transactions_for="rl-acc-a")
    with _sync_patches(provider):
        conn, _, outcome = await sync_connection(session, conn.id, workspace_id, user_id)

    assert outcome == "partial"
    assert conn.last_sync_failure is None


@pytest.mark.asyncio
async def test_a_sync_the_bank_throttled_entirely_notes_rate_limited(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    provider = _mock_provider_two_accounts(rate_limit_session=True)

    with _sync_patches(provider):
        conn, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "rate_limited"
    assert conn.last_sync_failure is not None
    assert conn.last_sync_failure["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_the_connections_api_returns_the_failure(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    provider = _failing_provider(
        ProviderUserActionRequired("bank wants the user", code="psu_present_required")
    )

    with _sync_patches(provider):
        response = await client.post(
            f"/api/connections/{conn.id}/sync", headers=auth_headers
        )
    assert response.status_code == 409

    listed = await client.get("/api/connections", headers=auth_headers)
    assert listed.status_code == 200
    row = next(c for c in listed.json() if c["id"] == str(conn.id))
    assert row["last_sync_failure"]["code"] == "psu_present_required"
    assert row["last_sync_failure"]["at"]


def test_a_malformed_stored_failure_is_not_returned():
    conn = BankConnection(settings={LAST_SYNC_FAILURE_KEY: {"code": "bogus", "at": "x"}})
    assert conn.last_sync_failure is None
    conn.settings = {LAST_SYNC_FAILURE_KEY: {"code": "rate_limited", "at": "not a date"}}
    assert conn.last_sync_failure is None
