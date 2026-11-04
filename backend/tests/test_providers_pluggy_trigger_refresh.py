"""Tests for ``PluggyProvider.trigger_refresh``.

Pluggy's auto-sync only runs once per day, so on a user-initiated manual sync
we ask Pluggy to pull fresh data from the bank first. These tests cover the
state machine around ``PATCH /items/{id}`` + polling ``GET /items/{id}``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.providers import pluggy as pluggy_module
from app.providers.pluggy import PluggyProvider


def _http_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = text
    return resp


def _client_with(patch_resp: MagicMock, get_responses: list[MagicMock]) -> MagicMock:
    """Build a MagicMock AsyncClient where PATCH returns ``patch_resp`` and
    successive GETs walk through ``get_responses``."""
    client = MagicMock()
    client.patch = AsyncMock(return_value=patch_resp)
    client.get = AsyncMock(side_effect=get_responses)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def _run(client: MagicMock) -> str:
    provider = PluggyProvider()
    with (
        patch.object(PluggyProvider, "_headers", new=AsyncMock(return_value={"X-API-KEY": "k"})),
        patch("app.providers.pluggy.httpx.AsyncClient", return_value=client),
        patch("app.providers.pluggy.asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        return await provider.trigger_refresh({"item_id": "item-1"})


@pytest.mark.asyncio
async def test_refresh_happy_path_returns_refreshed():
    """PATCH 200 → poll shows UPDATING → UPDATED → "refreshed"."""
    patch_resp = _http_response(200, {})
    client = _client_with(
        patch_resp,
        [
            _http_response(200, {"status": "UPDATING"}),
            _http_response(200, {"status": "UPDATED", "executionStatus": "SUCCESS"}),
        ],
    )
    assert await _run(client) == "refreshed"


@pytest.mark.asyncio
async def test_refresh_missing_item_id_skipped():
    """Connections without an item_id (non-Pluggy, malformed) skip refresh."""
    provider = PluggyProvider()
    assert await provider.trigger_refresh({}) == "skipped"


@pytest.mark.asyncio
async def test_refresh_400_mfa_code_signals_user_action_needed():
    """Pluggy 400 with an MFA-related codeDescription → user must reconnect
    via the widget so the new MFA token can be entered."""
    client = _client_with(
        _http_response(
            400,
            {
                "code": 400,
                "codeDescription": "MFA_PARAMERTER_WAS_ALREADY_USED_ERROR",
                "message": "MFA parameter has to be updated from last execution",
            },
        ),
        [],
    )
    assert await _run(client) == "needs_user_action"


@pytest.mark.asyncio
async def test_refresh_400_meupluggy_is_soft_failure():
    """Items created via MeuPluggy return ``"MeuPluggy item cant be updated"``
    when we PATCH them. Treat as a soft failure — reconnect won't help."""
    client = _client_with(
        _http_response(
            400,
            {
                "code": 400,
                "message": "MeuPluggy item cant be updated",
                "errorId": "abc",
            },
        ),
        [],
    )
    assert await _run(client) == "failed"


@pytest.mark.asyncio
async def test_refresh_400_unknown_code_is_soft_failure():
    """Defensive: 400 with no codeDescription → assume transient, read cached."""
    client = _client_with(
        _http_response(400, {"code": 400, "message": "Something else"}),
        [],
    )
    assert await _run(client) == "failed"


@pytest.mark.asyncio
async def test_refresh_waiting_user_input_signals_user_action_needed():
    """Item enters WAITING_USER_INPUT after PATCH — surface so user reconnects."""
    client = _client_with(
        _http_response(200, {}),
        [_http_response(200, {"status": "WAITING_USER_INPUT"})],
    )
    assert await _run(client) == "needs_user_action"


@pytest.mark.asyncio
async def test_refresh_login_error_signals_user_action_needed():
    """LOGIN_ERROR → credentials no longer valid; user must reconnect."""
    client = _client_with(
        _http_response(200, {}),
        [_http_response(200, {"status": "LOGIN_ERROR"})],
    )
    assert await _run(client) == "needs_user_action"


@pytest.mark.asyncio
async def test_refresh_outdated_treats_as_failed():
    """OUTDATED is a soft failure — credentials were valid but the last
    execution errored. Reading cached data is still useful, so don't punish
    the user with a reconnect prompt."""
    client = _client_with(
        _http_response(200, {}),
        [_http_response(200, {"status": "OUTDATED", "executionStatus": "ERROR"})],
    )
    assert await _run(client) == "failed"


@pytest.mark.asyncio
async def test_refresh_unknown_status_treats_as_failed():
    """Defensive against Pluggy adding new statuses we don't know about."""
    client = _client_with(
        _http_response(200, {}),
        [_http_response(200, {"status": "SOMETHING_NEW"})],
    )
    assert await _run(client) == "failed"


@pytest.mark.asyncio
async def test_refresh_patch_http_error_returns_failed():
    """Network blip on the PATCH — proceed with cached data."""
    client = MagicMock()
    client.patch = AsyncMock(side_effect=httpx.HTTPError("boom"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    provider = PluggyProvider()
    with (
        patch.object(PluggyProvider, "_headers", new=AsyncMock(return_value={"X-API-KEY": "k"})),
        patch("app.providers.pluggy.httpx.AsyncClient", return_value=client),
    ):
        assert await provider.trigger_refresh({"item_id": "item-1"}) == "failed"


@pytest.mark.asyncio
async def test_refresh_poll_eventually_times_out():
    """Item stays UPDATING past the deadline → "failed", caller reads stale data."""
    # Patch the deadline to fire on the first iteration.
    patch_resp = _http_response(200, {})
    client = _client_with(
        patch_resp,
        [_http_response(200, {"status": "UPDATING"})] * 10,
    )
    provider = PluggyProvider()
    monotonic_values = iter([0.0, 0.0, 999.0])  # before patch, before sleep, after sleep

    def fake_monotonic() -> float:
        try:
            return next(monotonic_values)
        except StopIteration:
            return 999.0

    with (
        patch.object(PluggyProvider, "_headers", new=AsyncMock(return_value={"X-API-KEY": "k"})),
        patch("app.providers.pluggy.httpx.AsyncClient", return_value=client),
        patch("app.providers.pluggy.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch("app.providers.pluggy.time.monotonic", side_effect=fake_monotonic),
    ):
        assert await provider.trigger_refresh({"item_id": "item-1"}) == "failed"


@pytest.mark.asyncio
async def test_default_trigger_refresh_returns_skipped():
    """Providers that don't override get the no-op default."""
    from app.providers.base import BankProvider

    class _Stub(BankProvider):
        name = "stub"
        flow_type = "oauth"

        async def get_oauth_url(self, *a, **kw):  # type: ignore[override]
            return ""

        async def handle_oauth_callback(self, code):  # type: ignore[override]
            raise NotImplementedError

        async def get_accounts(self, credentials):  # type: ignore[override]
            return []

        async def get_transactions(self, *a, **kw):  # type: ignore[override]
            return []

        async def refresh_credentials(self, credentials):  # type: ignore[override]
            return credentials

    assert await _Stub().trigger_refresh({}) == "skipped"


# --- guard: existing pluggy module wiring is what the tests assume ---


def test_pluggy_terminal_statuses_cover_expected_values():
    """Sanity-check the constant the implementation polls against."""
    assert "UPDATED" in pluggy_module._PLUGGY_TERMINAL_STATUSES
    assert "WAITING_USER_INPUT" in pluggy_module._PLUGGY_TERMINAL_STATUSES
    assert "LOGIN_ERROR" in pluggy_module._PLUGGY_TERMINAL_STATUSES
    assert "OUTDATED" in pluggy_module._PLUGGY_TERMINAL_STATUSES
