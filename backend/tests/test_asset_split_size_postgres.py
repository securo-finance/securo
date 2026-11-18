"""The size check on a split against real PostgreSQL: SQLite stores any number,
so only here does a limit that doesn't match the columns fail."""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.asset import Asset
from app.schemas.asset import AssetTransactionCreate
from app.services import asset_transaction_service
from tests.test_asset_transaction_service import market_asset  # noqa: F401 -- rerun on PostgreSQL


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def _hold(session, workspace, asset: Asset, quantity: str) -> None:
    asset.last_price = Decimal("1")
    await session.commit()
    await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal(quantity), price=Decimal("1"), date=date(2026, 1, 1)),
    )


async def _split_by_three(session, workspace, asset: Asset):
    return await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(kind="split", old_units=1, new_units=3, date=date(2026, 2, 1)),
    )


@pytest.mark.asyncio
async def test_the_largest_holding_a_split_may_leave_fits_the_columns(
    session, test_workspace, market_asset  # noqa: F811
):
    await _hold(session, test_workspace, market_asset, "333333333.333333")

    await _split_by_three(session, test_workspace, market_asset)

    await session.refresh(market_asset)
    assert market_asset.units == Decimal("999999999.999999")


@pytest.mark.asyncio
async def test_one_step_past_it_is_refused_before_the_database_sees_it(
    session, test_workspace, market_asset  # noqa: F811
):
    await _hold(session, test_workspace, market_asset, "333333333.333334")

    with pytest.raises(HTTPException) as exc:
        await _split_by_three(session, test_workspace, market_asset)

    assert exc.value.status_code == 422
