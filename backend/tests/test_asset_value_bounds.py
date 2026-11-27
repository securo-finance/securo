"""Asset values the backend works out stay inside what the column holds.

`asset_values.amount` keeps 6 decimals and its own width — `Numeric(15, 6)`
today, 9 integer digits, fewer than the money columns. A growth rule
compounded for years, a provider balance in a weak currency or units × price
can go past that while every input fits; unbounded, the write failed at flush
with a raw `NumericValueOutOfRangeError` — a 500 instead of an answer (issue
#642).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import Numeric, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import MoneyOutOfRange, bounded_asset_value, max_asset_value
from app.models.asset import PROVIDER_VALUE_DATE_METADATA_KEY, Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.base import HoldingData
from app.schemas.asset import AssetCreate, AssetValueCreate
from app.services import asset_service
from app.services.asset_service import _generate_growth_values
from app.services.connection_service import (
    _ensure_historical_seed,
    _store_todays_snapshot,
    _upsert_asset_from_holding,
    _upsert_asset_value_for_today,
)

# One unit above what the column holds, whatever width it has today.
TOO_BIG = max_asset_value() + Decimal("0.000001")
# A value that fits the column, which the growth rule then compounds past it.
GROWTH_BASE = (max_asset_value() / 1000).quantize(Decimal("0.000001"))


def test_the_ceiling_is_the_asset_column_not_a_constant_of_its_own():
    """The bound follows `asset_values.amount`: a migration that widens the
    column (the one in #1951) has to move the bound with it, and a hardcoded
    ceiling would keep refusing what the column already holds."""
    column = AssetValue.__table__.c.amount.type
    assert isinstance(column, Numeric)
    assert column.precision is not None and column.scale is not None
    integer_digits = column.precision - column.scale
    assert max_asset_value() == Decimal(10) ** integer_digits - Decimal(1).scaleb(-column.scale)
    assert len(str(int(max_asset_value()))) == integer_digits


def test_bounded_asset_value_keeps_the_column_ceiling():
    assert bounded_asset_value(max_asset_value()) == max_asset_value()
    assert bounded_asset_value(-max_asset_value()) == -max_asset_value()
    for value in (TOO_BIG, Decimal("1E+15"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("NaN")):
        with pytest.raises(MoneyOutOfRange):
            bounded_asset_value(value)


def test_the_refusal_names_the_value_the_column_holds():
    with pytest.raises(MoneyOutOfRange, match=f"at most {max_asset_value()}"):
        bounded_asset_value(TOO_BIG, "purchase_price")


def test_a_manual_value_past_the_column_is_refused_before_the_database():
    today = date.today()
    assert AssetValueCreate(amount=max_asset_value(), date=today).amount == max_asset_value()
    assert AssetValueCreate(amount=-max_asset_value(), date=today).amount == -max_asset_value()
    for value in (TOO_BIG, -TOO_BIG, Decimal("Infinity"), Decimal("-Infinity"), Decimal("NaN")):
        with pytest.raises(ValidationError):
            AssetValueCreate(amount=value, date=today)


def test_growth_backfill_refuses_a_series_past_the_column():
    """The helper behind create and update: a base the column holds, compounded
    into values it does not, raises instead of returning a row for the flush."""
    start = date.today() - timedelta(days=400)
    with pytest.raises(MoneyOutOfRange):
        _generate_growth_values(
            asset_id=uuid.uuid4(),
            base_amount=float(GROWTH_BASE),
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
    """A base that fits, doubling every month for more than a year: refused,
    and no half-created asset is left behind."""
    start = date.today() - timedelta(days=400)
    data = AssetCreate(
        name="Growth Overflow",
        type="investment",
        currency="BRL",
        valuation_method="growth_rule",
        purchase_price=GROWTH_BASE,
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
    start = date.today() - timedelta(days=400)
    response = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={
            "name": "Growth Overflow API",
            "type": "investment",
            "currency": "BRL",
            "valuation_method": "growth_rule",
            "purchase_price": str(GROWTH_BASE),
            "purchase_date": start.isoformat(),
            "growth_type": "percentage",
            "growth_rate": "100",
            "growth_frequency": "monthly",
            "growth_start_date": start.isoformat(),
        },
    )
    assert response.status_code == 422
    assert str(max_asset_value()) in response.json()["detail"]

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
    """A price the column holds and a quantity it holds, whose product it does
    not: each of them fits its own column, so only the product can be checked."""
    price = (max_asset_value() / 10).quantize(Decimal("0.000001"))
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Overflowing Quote",
        type="investment",
        currency="BRL",
        valuation_method="market_price",
        units=Decimal("20"),
    )
    session.add(asset)
    await session.flush()

    await asset_service.upsert_today_value(session, asset, price)
    await session.flush()

    stored = await session.scalar(
        select(func.count(AssetValue.id)).where(AssetValue.asset_id == asset.id)
    )
    assert stored == 0


@pytest.mark.asyncio
async def test_the_route_refuses_a_negative_value_past_the_column(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
):
    """The bound has two sides. `-1E+15` passed the old one-sided ceiling and
    went straight to the column, where PostgreSQL answered 500."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Negative Overflow API",
        type="investment",
        currency="IDR",
        valuation_method="manual",
    )
    session.add(asset)
    await session.commit()

    response = await client.post(
        f"/api/assets/{asset.id}/values",
        headers=auth_headers,
        json={"amount": str(-TOO_BIG), "date": date.today().isoformat()},
    )
    assert response.status_code == 422

    stored = await session.scalar(
        select(func.count(AssetValue.id)).where(AssetValue.asset_id == asset.id)
    )
    assert stored == 0


@pytest.mark.asyncio
async def test_a_refused_snapshot_does_not_move_the_provider_value_date(
    session: AsyncSession, test_user: User, test_workspace
):
    """The metadata blob is replaced before the value is checked, so the
    payload's date used to end up over the amount that was kept."""
    previous = date(2026, 1, 15)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Weak Currency Snapshot",
        type="investment",
        currency="IDR",
        valuation_method="manual",
        external_metadata={PROVIDER_VALUE_DATE_METADATA_KEY: previous.isoformat()},
    )
    session.add(asset)
    await session.flush()
    today = date.today()
    holding = HoldingData(
        external_id="weak-currency-snapshot",
        name="Weak Currency Snapshot",
        currency="IDR",
        current_value=TOO_BIG,
        value_date=today,
    )

    await _upsert_asset_from_holding(
        session,
        asset,
        holding,
        test_user.id,
        uuid.uuid4(),
        "pluggy",
        workspace_id=test_workspace.id,
    )
    assert (asset.external_metadata or {})[PROVIDER_VALUE_DATE_METADATA_KEY] == today.isoformat()

    await _store_todays_snapshot(session, asset, holding, today)

    assert PROVIDER_VALUE_DATE_METADATA_KEY not in (asset.external_metadata or {})


@pytest.mark.asyncio
async def test_a_stored_snapshot_keeps_the_provider_value_date(
    session: AsyncSession, test_user: User, test_workspace
):
    """The other side of the same rule: a value that fits keeps its date."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Stored Snapshot",
        type="investment",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    today = date.today()
    holding = HoldingData(
        external_id="stored-snapshot",
        name="Stored Snapshot",
        currency="BRL",
        current_value=Decimal("1500.000000"),
        value_date=today,
    )

    await _upsert_asset_from_holding(
        session,
        asset,
        holding,
        test_user.id,
        uuid.uuid4(),
        "pluggy",
        workspace_id=test_workspace.id,
    )
    await _store_todays_snapshot(session, asset, holding, today)

    assert (asset.external_metadata or {})[PROVIDER_VALUE_DATE_METADATA_KEY] == today.isoformat()
    stored = await session.scalar(
        select(func.count(AssetValue.id)).where(AssetValue.asset_id == asset.id)
    )
    assert stored == 1
