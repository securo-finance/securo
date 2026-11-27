"""Asset values past the column are refused, not half-written (PostgreSQL).

SQLite keeps numbers as the driver gives them, so only PostgreSQL shows what
the bound protects: the value reached the column as a
`NumericValueOutOfRangeError` — a 500 on a request that passed validation
(issue #642). The values here are taken from the column itself, so the test
holds whatever width `asset_values.amount` has.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.money import MoneyOutOfRange, max_asset_value
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.schemas.asset import AssetCreate
from app.services import asset_service

pytestmark = pytest.mark.asyncio

# A base the column holds, which the rule then compounds past it.
GROWTH_BASE = (max_asset_value() / 1000).quantize(Decimal("0.000001"))


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused fixtures start in a fresh per-test PostgreSQL schema."""


async def test_an_exploding_growth_rule_refuses_and_writes_nothing(
    session, postgres_sessions, test_user, test_workspace
):
    start = date.today() - timedelta(days=400)
    data = AssetCreate(
        name="Growth Overflow",
        type="investment",
        currency="BRL",
        valuation_method="growth_rule",
        purchase_price=GROWTH_BASE,  # inside the column
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("100.0"),  # doubles every month: past it within a year
        growth_frequency="monthly",
        growth_start_date=start,
    )
    with pytest.raises(MoneyOutOfRange):
        await asset_service.create_asset(session, test_workspace.id, test_user.id, data)
    await session.rollback()

    async with postgres_sessions() as other:
        assert await other.scalar(select(func.count(Asset.id)).where(Asset.name == "Growth Overflow")) == 0
        assert await other.scalar(select(func.count(AssetValue.id))) == 0


async def test_a_series_inside_the_column_is_still_written(
    session, postgres_sessions, test_user, test_workspace
):
    """The bound only refuses what the column cannot hold."""
    start = date.today() - timedelta(days=200)
    data = AssetCreate(
        name="Growth Inside",
        type="investment",
        currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("1000.00"),
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("1.0"),
        growth_frequency="monthly",
        growth_start_date=start,
    )
    read = await asset_service.create_asset(session, test_workspace.id, test_user.id, data)
    assert read.value_count >= 2
