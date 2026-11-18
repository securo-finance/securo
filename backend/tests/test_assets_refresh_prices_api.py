"""Tests for the portfolio-wide price refresh endpoint (issue #778).

The scheduled Celery task already refreshes every market-priced asset in the
database. The endpoint the Assets page calls must not: it has to behave like
the user refreshing their own portfolio, and never touch another workspace's
rows (ADR 0002).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.market_price import MarketPriceProvider, MarketPriceRateLimitedError
from app.schemas.asset import MarketSymbolQuote


class FakeMarketProvider(MarketPriceProvider):
    """Provider that answers from a canned dict and counts its calls."""

    name = "fake"

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices
        self.batch_calls = 0
        self.requested: list[str] = []
        self.rate_limited = False

    async def search(self, query: str, limit: int = 20):
        return []

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        price = self._prices.get(symbol.upper())
        if price is None:
            return None
        return MarketSymbolQuote(
            symbol=symbol.upper(),
            name=f"{symbol.upper()} Inc",
            price=float(price),
            currency="USD",
        )

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        self.batch_calls += 1
        self.requested = [s.upper() for s in symbols]
        if self.rate_limited:
            raise MarketPriceRateLimitedError("slow down")
        return {s.upper(): self._prices.get(s.upper()) for s in symbols}


async def _other_workspace(session: AsyncSession, user_id: uuid.UUID) -> Workspace:
    ws = Workspace(
        id=uuid.uuid4(),
        name="Other portfolio",
        kind="personal",
        created_by_user_id=user_id,
        default_currency="USD",
    )
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=user_id, role="owner")
    )
    await session.commit()
    await session.refresh(ws)
    return ws


async def _market_asset(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    ticker: str,
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        type="investment",
        currency="USD",
        valuation_method="market_price",
        ticker=ticker,
        units=Decimal("1"),
        purchase_date=date(2025, 1, 2),
        position=0,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest_asyncio.fixture
async def portfolio(session: AsyncSession, test_user: User, test_workspace: Workspace):
    mine = await _market_asset(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Mine",
        ticker="AAPL",
    )
    other_ws = await _other_workspace(session, test_user.id)
    theirs = await _market_asset(
        session,
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Theirs",
        ticker="MSFT",
    )
    return mine, theirs, other_ws


@pytest.mark.asyncio
async def test_refresh_prices_only_touches_the_current_workspace(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    portfolio,
):
    mine, theirs, _ = portfolio
    provider = FakeMarketProvider({"AAPL": Decimal("180.00"), "MSFT": Decimal("400.00")})
    with patch("app.services.asset_service.get_market_price_provider", return_value=provider):
        resp = await client.post("/api/assets/refresh-prices", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"refreshed": 1, "skipped": 0, "rate_limited": 0}
    # The other workspace's ticker never reaches the provider.
    assert provider.requested == ["AAPL"]

    await session.refresh(mine)
    await session.refresh(theirs)
    assert mine.last_price == Decimal("180.00")
    assert theirs.last_price is None


@pytest.mark.asyncio
async def test_refresh_prices_without_market_assets_is_a_no_op(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_workspace: Workspace
):
    resp = await client.post("/api/assets/refresh-prices", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"refreshed": 0, "skipped": 0, "rate_limited": 0}


@pytest.mark.asyncio
async def test_refresh_prices_rate_limited_429(
    client: AsyncClient, auth_headers: dict, portfolio
):
    provider = FakeMarketProvider({"AAPL": Decimal("180.00")})
    provider.rate_limited = True
    with patch("app.services.asset_service.get_market_price_provider", return_value=provider):
        resp = await client.post("/api/assets/refresh-prices", headers=auth_headers)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_refresh_prices_requires_write_access(
    client: AsyncClient, viewer_auth_headers: dict, portfolio
):
    mine, theirs, _ = portfolio
    resp = await client.post("/api/assets/refresh-prices", headers=viewer_auth_headers)
    assert resp.status_code == 403
