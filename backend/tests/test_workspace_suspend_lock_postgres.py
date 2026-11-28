"""A suspension waits for a price sweep that already holds the workspace (ADR 0052).

SQLite has no row locks, so there a suspension cannot land in the middle of a
sweep. Here the sweep is paused after it re-read its holdings and before it
writes, and an administrator suspends the workspace from another session. The
suspension has to wait for the sweep's commit; without `hold_active` it
commits at once and the sweep then writes a price into a suspended workspace.
"""

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.market_price import LatestClose, MarketPriceProvider
from app.schemas.asset import MarketSymbolMatch, MarketSymbolQuote
from app.services import asset_service, workspace_status_service


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


class _Closes(MarketPriceProvider):
    """A feed that answers the batch with one close."""

    name = "fake"

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_latest_closes(self, symbols: list[str]) -> dict[str, Optional[LatestClose]]:
        return {s.upper(): LatestClose(Decimal("42.00"), date(2026, 11, 26)) for s in symbols}

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        return None


def _user(name, **over):
    return User(
        id=uuid.uuid4(), email=f"{name}@example.invalid", hashed_password="unused",
        is_active=True, is_verified=True, preferences={"currency_display": "BRL"}, **over,
    )


async def _seed(sessions):
    owner, admin = _user("owner"), _user("admin", is_superuser=True)
    workspace = Workspace(id=uuid.uuid4(), name="Held")
    async with sessions() as session:
        session.add_all([owner, admin, workspace])
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        asset = Asset(
            id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id,
            name="Petrobras", type="stock", currency="BRL",
            valuation_method="market_price", ticker="PETR4.SA", last_price=Decimal("30.00"),
        )
        session.add(asset)
        await session.commit()
    return workspace.id, admin.id, asset.id


@pytest.mark.asyncio
async def test_a_suspension_waits_for_the_price_sweep_that_holds_the_workspace(
    postgres_sessions, clean_db, monkeypatch,
):
    workspace_id, admin_id, asset_id = await _seed(postgres_sessions)
    reached, release = asyncio.Event(), asyncio.Event()
    real_split_dates = asset_service._last_split_dates

    async def paused(session, asset_ids):
        # Runs after the holdings were re-read and locked, before any write.
        reached.set()
        await release.wait()
        return await real_split_dates(session, asset_ids)

    monkeypatch.setattr(asset_service, "_last_split_dates", paused)

    async with postgres_sessions() as sweep, postgres_sessions() as admin:
        sweeping = asyncio.create_task(
            asset_service.refresh_all_market_prices(sweep, market_provider=_Closes())
        )
        await asyncio.wait_for(reached.wait(), timeout=30)

        suspending = asyncio.create_task(
            workspace_status_service.set_status(
                admin, workspace_id, "suspended", actor_id=admin_id, reason="Unpaid invoice",
            )
        )
        await asyncio.sleep(0.5)
        # The sweep holds the workspace active until it commits.
        assert not suspending.done()

        release.set()
        summary = await asyncio.wait_for(sweeping, timeout=30)
        await asyncio.wait_for(suspending, timeout=30)

    assert summary["refreshed"] == 1
    async with postgres_sessions() as later:
        price = await later.scalar(select(Asset.last_price).where(Asset.id == asset_id))
        status = await later.scalar(select(Workspace.status).where(Workspace.id == workspace_id))
    # The price written belongs to the moment before the suspension.
    assert price == Decimal("42.00")
    assert status == "suspended"
