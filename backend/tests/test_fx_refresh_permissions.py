"""The manual FX refresh is an instance-wide provider call (issue #636).

Any signed-in user used to reach the route. One account in a loop could spend
the instance's monthly provider quota, and every success also cleared the 429
pause that protects every customer's conversions. The route is now an admin
action, reuses a day already stored, and names the quota when the provider
refuses the request.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.fx_rate import FxRate
from app.providers.openexchangerates import FxProviderQuotaExceeded
from app.services import fx_rate_service


@pytest.fixture
def configured_app_id():
    """An app id is set, so the route goes past the configuration check."""
    with patch.object(get_settings(), "openexchangerates_app_id", "test-app-id"):
        yield


@pytest_asyncio.fixture
async def todays_rates(session: AsyncSession, clean_db) -> None:
    """Rates already stored for today, as the daily sync leaves them."""
    for currency, rate in (("BRL", "5.00"), ("EUR", "0.92")):
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency=currency,
                date=app_today(),
                rate=Decimal(rate),
                source="test",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_refresh_rejects_a_non_admin(
    client: AsyncClient, auth_headers, configured_app_id
):
    sync = AsyncMock(return_value=5)
    with patch("app.services.fx_rate_service.sync_rates", new=sync):
        response = await client.post("/api/fx-rates/refresh", headers=auth_headers)

    assert response.status_code == 403
    # The refusal happens before the provider is reached.
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_syncs_for_the_admin(
    client: AsyncClient, admin_auth_headers, configured_app_id
):
    sync = AsyncMock(return_value=5)
    with patch("app.services.fx_rate_service.sync_rates", new=sync):
        response = await client.post(
            "/api/fx-rates/refresh", headers=admin_auth_headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["synced"] is True
    assert body["rates_count"] == 5
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_reuses_a_day_already_stored(
    client: AsyncClient, admin_auth_headers, configured_app_id, todays_rates
):
    sync = AsyncMock(return_value=5)
    with patch("app.services.fx_rate_service.sync_rates", new=sync):
        response = await client.post(
            "/api/fx-rates/refresh", headers=admin_auth_headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["synced"] is False
    assert body["skipped"] is True
    assert body["rates_count"] == 2
    # No new call leaves the instance for a day it already has.
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_exceeded_is_reported_as_a_quota_problem(
    client: AsyncClient, admin_auth_headers, configured_app_id
):
    sync = AsyncMock(
        side_effect=FxProviderQuotaExceeded("exchange-rate provider quota exceeded")
    )
    with patch("app.services.fx_rate_service.sync_rates", new=sync):
        response = await client.post(
            "/api/fx-rates/refresh", headers=admin_auth_headers
        )

    assert response.status_code == 429
    detail = response.json()["detail"].lower()
    assert "quota" in detail
    # The old text sent the admin to check a credential that was fine.
    assert "app id" not in detail


@pytest.mark.asyncio
async def test_non_admin_cannot_clear_the_quota_pause(
    client: AsyncClient, auth_headers, configured_app_id
):
    fx_rate_service.reset_fx_failure_cache()
    try:
        fx_rate_service.remember_quota_exceeded(app_today())
        assert fx_rate_service._fetch_blocked(app_today()) is True

        with patch("app.services.fx_rate_service.sync_rates", new=AsyncMock(return_value=5)):
            response = await client.post(
                "/api/fx-rates/refresh", headers=auth_headers
            )

        assert response.status_code == 403
        assert fx_rate_service._fetch_blocked(app_today()) is True
    finally:
        fx_rate_service.reset_fx_failure_cache()


@pytest.mark.asyncio
async def test_admin_refresh_clears_the_pause_it_no_longer_needs(
    client: AsyncClient, admin_auth_headers, configured_app_id
):
    """A person asking for fresh rates ends a pause remembered from a 429."""
    fx_rate_service.reset_fx_failure_cache()
    try:
        fx_rate_service.remember_quota_exceeded(app_today())

        with patch("app.services.fx_rate_service.sync_rates", new=AsyncMock(return_value=5)):
            response = await client.post(
                "/api/fx-rates/refresh", headers=admin_auth_headers
            )

        assert response.status_code == 200
        assert fx_rate_service._fetch_blocked(app_today()) is False
    finally:
        fx_rate_service.reset_fx_failure_cache()
