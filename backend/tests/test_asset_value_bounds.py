"""Asset values the backend works out stay inside what the column holds.

`asset_values.amount` keeps 13 integer digits. A growth rule compounded for
years, a provider balance in a weak currency or units × price can go past it
while every input fits; unbounded, the write failed at flush with a raw
`NumericValueOutOfRangeError` — a 500 instead of an answer (issue #642).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import MAX_MONEY_AMOUNT, MoneyOutOfRange, bounded_asset_value
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.schemas.asset import AssetCreate, AssetValueCreate
from app.services import asset_service
from app.services.asset_service import _generate_growth_values
from app.services.connection_service import (
    _ensure_historical_seed,
    _upsert_asset_value_for_today,
)

TOO_BIG = MAX_MONEY_AMOUNT + Decimal("1")


def test_bounded_asset_value_keeps_the_column_ceiling():
    assert bounded_asset_value(MAX_MONEY_AMOUNT) == MAX_MONEY_AMOUNT
    assert bounded_asset_value(Decimal("-9999999999999.99")) == Decimal("-9999999999999.99")
    for value in (TOO_BIG, Decimal("1E+15"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("NaN")):
        with pytest.raises(MoneyOutOfRange):
            bounded_asset_value(value)


def test_a_manual_value_past_the_column_is_refused_before_the_database():
    assert AssetValueCreate(amount=MAX_MONEY_AMOUNT, date=date.today()).amount == MAX_MONEY_AMOUNT
    with pytest.raises(ValidationError):
        AssetValueCreate(amount=MAX_MONEY_AMOUNT + Decimal("0.01"), date=date.today())


def test_growth_backfill_refuses_a_series_past_the_column():
    """The helper behind create and update: it raises instead of returning a row
    the column cannot hold."""
    start = date.today() - timedelta(days=400)
    with pytest.raises(MoneyOutOfRange):
        _generate_growth_values(
            asset_id=uuid.uuid4(),
            base_amount=float(Decimal("1000000000000")),
            base_date=start,
            growth_type="percentage",
            growth_rate=100.0,
            growth_frequency="monthly",
            growth_start_date=start,
        )


@pytest.mark.asyncio
async def test_create_asset_with_a_growth_rule_past_the_column_is_refused(
    session: AsyncSession, test_user: User, test_workspace
):
    """A 1e12 asset doubling every month passes 1e13 within months: refused,
    and no half-created asset is left behind."""
    start = date.today() - timedelta(days=200)
    data = AssetCreate(
        name="Growth Overflow",
        type="investment",
        currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("1000000000000.00"),
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("100.0"),
        growth_frequency="monthly",
        growth_start_date=start,
    )
    with pytest.raises(MoneyOutOfRange):
        await asset_service.create_asset(session, test_workspace.id, test_user.id, data)
    await session.rollback()

    stored = await session.scalar(
        select(func.count(Asset.id)).where(Asset.name == "Growth Overflow")
    )
    assert stored == 0


@pytest.mark.asyncio
async def test_the_route_answers_422_instead_of_500(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    start = date.today() - timedelta(days=200)
    response = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={
            "name": "Growth Overflow API",
            "type": "investment",
            "currency": "BRL",
            "valuation_method": "growth_rule",
            "purchase_price": "1000000000000.00",
            "purchase_date": start.isoformat(),
            "growth_type": "percentage",
            "growth_rate": "100",
            "growth_frequency": "monthly",
            "growth_start_date": start.isoformat(),
        },
    )
    assert response.status_code == 422
    assert str(MAX_MONEY_AMOUNT) in response.json()["detail"]

    listed = await client.get("/api/assets", headers=auth_headers)
    assert all(a["name"] != "Growth Overflow API" for a in listed.json())


@pytest.mark.asyncio
async def test_a_synced_balance_past_the_column_keeps_the_row_it_had(
    session: AsyncSession, test_user: User, test_workspace
):
    """A weak-currency holding the column cannot hold must not fail the sync of
    the whole connection: its row keeps the value it had."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Weak Currency Holding",
        type="investment",
        currency="IDR",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    today = date.today()
    session.add(
        AssetValue(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("100.000000"),
            date=today,
            source="sync",
        )
    )
    await session.flush()

    await _upsert_asset_value_for_today(session, asset, TOO_BIG, today)
    await session.flush()

    rows = (
        await session.execute(select(AssetValue).where(AssetValue.asset_id == asset.id))
    ).scalars().all()
    assert [row.amount for row in rows] == [Decimal("100.000000")]


@pytest.mark.asyncio
async def test_a_synced_purchase_price_past_the_column_is_not_seeded(
    session: AsyncSession, test_user: User, test_workspace
):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Weak Currency Seed",
        type="investment",
        currency="IDR",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()

    await _ensure_historical_seed(session, asset, date(2020, 1, 1), TOO_BIG)
    await session.flush()

    stored = await session.scalar(
        select(func.count(AssetValue.id)).where(AssetValue.asset_id == asset.id)
    )
    assert stored == 0


@pytest.mark.asyncio
async def test_todays_market_value_is_not_written_when_units_times_price_overflows(
    session: AsyncSession, test_user: User, test_workspace
):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Overflowing Quote",
        type="investment",
        currency="BRL",
        valuation_method="market_price",
        units=Decimal("1000000"),
    )
    session.add(asset)
    await session.flush()

    await asset_service.upsert_today_value(session, asset, Decimal("100000000"))
    await session.flush()

    stored = await session.scalar(
        select(func.count(AssetValue.id)).where(AssetValue.asset_id == asset.id)
    )
    assert stored == 0
