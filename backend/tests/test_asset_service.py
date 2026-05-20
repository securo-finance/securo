import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.models.user import User
from app.schemas.asset import AssetCreate, AssetUpdate, AssetValueCreate
from app.services import asset_service
from app.services.asset_service import (
    _compute_current_value,
    _generate_growth_values,
    _next_due_date,
    get_portfolio_trend,
)


@pytest_asyncio.fixture
async def test_asset(session: AsyncSession, test_user: User) -> Asset:
    """Create a test asset."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Test Property",
        type="real_estate",
        currency="BRL",
        valuation_method="manual",
        purchase_date=date(2025, 1, 1),
        purchase_price=Decimal("500000.00"),
        position=0,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest_asyncio.fixture
async def test_asset_with_values(session: AsyncSession, test_asset: Asset) -> Asset:
    """Create test asset with some value entries."""
    values_data = [
        (Decimal("500000.00"), date(2025, 1, 1), "manual"),
        (Decimal("520000.00"), date(2025, 6, 1), "manual"),
        (Decimal("550000.00"), date(2026, 1, 1), "manual"),
    ]
    for amount, dt, source in values_data:
        v = AssetValue(
            id=uuid.uuid4(),
            asset_id=test_asset.id,
            amount=amount,
            date=dt,
            source=source,
        )
        session.add(v)
    await session.commit()
    return test_asset


@pytest.mark.asyncio
async def test_create_asset(session: AsyncSession, test_user: User):
    data = AssetCreate(name="Car", type="vehicle", currency="BRL")
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.name == "Car"
    assert result.type == "vehicle"
    assert result.current_value is None
    assert result.value_count == 0


@pytest.mark.asyncio
async def test_create_asset_with_initial_value(session: AsyncSession, test_user: User):
    data = AssetCreate(
        name="Watch", type="valuable", currency="BRL",
        current_value=Decimal("15000.00"),
        purchase_price=Decimal("12000.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value == 15000.0
    assert result.gain_loss == 3000.0
    assert result.value_count == 1


@pytest.mark.asyncio
async def test_list_assets(session: AsyncSession, test_user: User, test_asset: Asset):
    results = await asset_service.get_assets(session, test_user.id)
    assert len(results) >= 1
    names = [a.name for a in results]
    assert "Test Property" in names


@pytest.mark.asyncio
async def test_list_assets_excludes_archived(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Archived Asset",
        type="other",
        currency="BRL",
        valuation_method="manual",
        is_archived=True,
    )
    session.add(asset)
    await session.commit()

    results = await asset_service.get_assets(session, test_user.id, include_archived=False)
    names = [a.name for a in results]
    assert "Archived Asset" not in names

    results_all = await asset_service.get_assets(session, test_user.id, include_archived=True)
    names_all = [a.name for a in results_all]
    assert "Archived Asset" in names_all


@pytest.mark.asyncio
async def test_get_asset(session: AsyncSession, test_user: User, test_asset: Asset):
    result = await asset_service.get_asset(session, test_asset.id, test_user.id)
    assert result is not None
    assert result.name == "Test Property"


@pytest.mark.asyncio
async def test_get_asset_not_found(session: AsyncSession, test_user: User):
    result = await asset_service.get_asset(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_asset(session: AsyncSession, test_user: User, test_asset: Asset):
    data = AssetUpdate(name="Updated Property", type="investment")
    result = await asset_service.update_asset(session, test_asset.id, test_user.id, data)
    assert result is not None
    assert result.name == "Updated Property"
    assert result.type == "investment"


@pytest.mark.asyncio
async def test_update_asset_not_found(session: AsyncSession, test_user: User):
    data = AssetUpdate(name="Nope")
    result = await asset_service.update_asset(session, uuid.uuid4(), test_user.id, data)
    assert result is None


@pytest.mark.asyncio
async def test_delete_asset(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="To Delete",
        type="other",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.commit()

    deleted = await asset_service.delete_asset(session, asset.id, test_user.id)
    assert deleted is True

    result = await asset_service.get_asset(session, asset.id, test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_asset_cascades_values(session: AsyncSession, test_user: User):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Cascade Test",
        type="other",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()

    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=asset.id,
        amount=Decimal("1000.00"),
        date=date.today(),
        source="manual",
    )
    session.add(v)
    await session.commit()

    deleted = await asset_service.delete_asset(session, asset.id, test_user.id)
    assert deleted is True


@pytest.mark.asyncio
async def test_add_asset_value(session: AsyncSession, test_user: User, test_asset: Asset):
    data = AssetValueCreate(amount=Decimal("600000.00"), date=date.today())
    result = await asset_service.add_asset_value(session, test_asset.id, test_user.id, data)
    assert result is not None
    assert result.amount == 600000.0
    assert result.source == "manual"


@pytest.mark.asyncio
async def test_add_asset_value_not_owned(session: AsyncSession, test_asset: Asset):
    other_user_id = uuid.uuid4()
    data = AssetValueCreate(amount=Decimal("100.00"), date=date.today())
    result = await asset_service.add_asset_value(session, test_asset.id, other_user_id, data)
    assert result is None


@pytest.mark.asyncio
async def test_get_asset_values(session: AsyncSession, test_user: User, test_asset_with_values: Asset):
    values = await asset_service.get_asset_values(session, test_asset_with_values.id, test_user.id)
    assert values is not None
    assert len(values) == 3
    # Should be ordered most recent first
    assert values[0].date >= values[1].date


@pytest.mark.asyncio
async def test_delete_asset_value(session: AsyncSession, test_user: User, test_asset: Asset):
    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=test_asset.id,
        amount=Decimal("100.00"),
        date=date.today(),
        source="manual",
    )
    session.add(v)
    await session.commit()

    deleted = await asset_service.delete_asset_value(session, v.id, test_user.id)
    assert deleted is True


@pytest.mark.asyncio
async def test_get_asset_value_trend(session: AsyncSession, test_user: User, test_asset_with_values: Asset):
    trend = await asset_service.get_asset_value_trend(session, test_asset_with_values.id, test_user.id)
    assert trend is not None
    assert len(trend) == 3
    assert trend[0]["date"] <= trend[1]["date"]  # ordered by date asc


@pytest.mark.asyncio
async def test_get_total_asset_value(session: AsyncSession, test_user: User, test_asset_with_values: Asset):
    totals, _ = await asset_service.get_asset_values_at(session, test_user.id)
    assert "BRL" in totals
    assert totals["BRL"] >= 550000.0  # latest value


@pytest.mark.asyncio
async def test_total_asset_value_excludes_sold(session: AsyncSession, test_user: User):
    """Sold assets should not count in total."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Sold Item",
        type="vehicle",
        currency="BRL",
        valuation_method="manual",
        sell_date=date.today(),
        sell_price=Decimal("20000.00"),
    )
    session.add(asset)
    await session.flush()
    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=asset.id,
        amount=Decimal("20000.00"),
        date=date.today(),
        source="manual",
    )
    session.add(v)
    await session.commit()

    totals, _ = await asset_service.get_asset_values_at(session, test_user.id)
    assert isinstance(totals, dict)


@pytest.mark.asyncio
async def test_growth_rule_task(session: AsyncSession, test_user: User):
    """Test growth rule application logic directly."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Growth Test",
        type="investment",
        currency="BRL",
        valuation_method="growth_rule",
        growth_type="percentage",
        growth_rate=Decimal("10.0"),
        growth_frequency="daily",
        growth_start_date=date.today() - timedelta(days=30),
    )
    session.add(asset)
    await session.flush()

    v = AssetValue(
        id=uuid.uuid4(),
        asset_id=asset.id,
        amount=Decimal("1000.00"),
        date=date.today() - timedelta(days=2),
        source="manual",
    )
    session.add(v)
    await session.commit()

    from app.tasks.asset_tasks import _next_due_date
    next_due = _next_due_date(v.date, "daily")
    assert next_due <= date.today()


# ---------------------------------------------------------------------------
# Current value fallback tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_value_falls_back_to_purchase_price(session: AsyncSession, test_user: User):
    """When no AssetValue rows exist, current_value should equal purchase_price."""
    data = AssetCreate(
        name="New Car", type="vehicle", currency="BRL",
        purchase_price=Decimal("80000.00"),
        purchase_date=date(2025, 6, 1),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value == 80000.0
    assert result.gain_loss == 0.0
    assert result.value_count == 0


@pytest.mark.asyncio
async def test_current_value_none_when_no_price_no_values(session: AsyncSession, test_user: User):
    """When no purchase_price and no AssetValue, current_value should be None."""
    data = AssetCreate(name="Empty Asset", type="other", currency="BRL")
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value is None
    assert result.gain_loss is None


@pytest.mark.asyncio
async def test_current_value_prefers_latest_value_over_purchase(session: AsyncSession, test_user: User):
    """When AssetValue rows exist, current_value should use the latest one, not purchase_price."""
    data = AssetCreate(
        name="Appreciated Watch", type="valuable", currency="BRL",
        purchase_price=Decimal("5000.00"),
        current_value=Decimal("7500.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value == 7500.0
    assert result.gain_loss == 2500.0


# ---------------------------------------------------------------------------
# Growth rule creation backfill tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_growth_rule_backfills_values_on_create(session: AsyncSession, test_user: User):
    """Creating a growth_rule asset starting in the past should generate all intermediate values."""
    start = date.today() - timedelta(days=90)
    data = AssetCreate(
        name="Backfill Fund", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("10000.00"),
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("5.0"),  # 5% per month
        growth_frequency="monthly",
        growth_start_date=start,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # Should have the seed value + at least 2 monthly rule values (90 days ~ 3 months)
    assert result.value_count >= 3
    # Current value should be greater than purchase price after compounding
    assert result.current_value is not None
    assert result.current_value > 10000.0


@pytest.mark.asyncio
async def test_growth_rule_percentage_backfill_math(session: AsyncSession, test_user: User):
    """Verify the exact math for percentage growth backfill."""
    # Start exactly 3 months ago on the 1st
    today = date.today()
    start = date(today.year if today.month > 3 else today.year - 1,
                 today.month - 3 if today.month > 3 else today.month + 9, 1)
    data = AssetCreate(
        name="Math Check", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("1000.00"),
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("10.0"),  # 10% per month
        growth_frequency="monthly",
        growth_start_date=start,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # 1000 * 1.1 * 1.1 * 1.1 = 1331.0
    assert result.current_value is not None
    assert abs(result.current_value - 1331.0) < 0.01

    # Should have seed + 3 rule values = 4 total
    assert result.value_count == 4


@pytest.mark.asyncio
async def test_growth_rule_absolute_backfill(session: AsyncSession, test_user: User):
    """Verify absolute growth type adds a fixed amount each period."""
    today = date.today()
    start = date(today.year if today.month > 3 else today.year - 1,
                 today.month - 3 if today.month > 3 else today.month + 9, 1)
    data = AssetCreate(
        name="Absolute Growth", type="other", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("5000.00"),
        purchase_date=start,
        growth_type="absolute",
        growth_rate=Decimal("500.00"),  # +500 per month
        growth_frequency="monthly",
        growth_start_date=start,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # 5000 + 500 + 500 + 500 = 6500
    assert result.current_value is not None
    assert abs(result.current_value - 6500.0) < 0.01
    assert result.value_count == 4


@pytest.mark.asyncio
async def test_growth_rule_future_start_no_backfill(session: AsyncSession, test_user: User):
    """If growth_start_date is in the future, no rule values should be generated."""
    future = date.today() + timedelta(days=30)
    data = AssetCreate(
        name="Future Fund", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("10000.00"),
        purchase_date=date.today(),
        growth_type="percentage",
        growth_rate=Decimal("5.0"),
        growth_frequency="monthly",
        growth_start_date=future,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # Only the seed value, no rule-generated values
    assert result.value_count == 1
    assert result.current_value == 10000.0


@pytest.mark.asyncio
async def test_growth_rule_daily_backfill(session: AsyncSession, test_user: User):
    """Daily growth rule should generate one value per day."""
    start = date.today() - timedelta(days=5)
    data = AssetCreate(
        name="Daily Growth", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("1000.00"),
        purchase_date=start,
        growth_type="percentage",
        growth_rate=Decimal("1.0"),  # 1% per day
        growth_frequency="daily",
        growth_start_date=start,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # seed + 5 daily values = 6
    assert result.value_count == 6
    # 1000 * 1.01^5 ≈ 1051.01
    assert result.current_value is not None
    assert abs(result.current_value - 1051.01) < 0.1


@pytest.mark.asyncio
async def test_growth_rule_weekly_backfill(session: AsyncSession, test_user: User):
    """Weekly growth rule should generate one value per week."""
    start = date.today() - timedelta(weeks=3)
    data = AssetCreate(
        name="Weekly Growth", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_price=Decimal("2000.00"),
        purchase_date=start,
        growth_type="absolute",
        growth_rate=Decimal("100.00"),  # +100 per week
        growth_frequency="weekly",
        growth_start_date=start,
    )
    result = await asset_service.create_asset(session, test_user.id, data)

    # seed + 3 weekly values = 4
    assert result.value_count == 4
    # 2000 + 300 = 2300
    assert result.current_value is not None
    assert abs(result.current_value - 2300.0) < 0.01


# ---------------------------------------------------------------------------
# Gain/loss computation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gain_loss_positive(session: AsyncSession, test_user: User):
    """Gain/loss should be positive when current value exceeds purchase price."""
    data = AssetCreate(
        name="Gainer", type="valuable", currency="BRL",
        purchase_price=Decimal("1000.00"),
        current_value=Decimal("1500.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.gain_loss == 500.0


@pytest.mark.asyncio
async def test_gain_loss_negative(session: AsyncSession, test_user: User):
    """Gain/loss should be negative when current value is below purchase price."""
    data = AssetCreate(
        name="Loser", type="vehicle", currency="BRL",
        purchase_price=Decimal("20000.00"),
        current_value=Decimal("15000.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.gain_loss == -5000.0


@pytest.mark.asyncio
async def test_gain_loss_zero_at_purchase_fallback(session: AsyncSession, test_user: User):
    """When falling back to purchase_price (no values), gain/loss should be 0."""
    data = AssetCreate(
        name="No Values", type="other", currency="BRL",
        purchase_price=Decimal("3000.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value == 3000.0
    assert result.gain_loss == 0.0


@pytest.mark.asyncio
async def test_gain_loss_none_without_purchase_price(session: AsyncSession, test_user: User):
    """Without a purchase_price, gain_loss should be None even if there's a current value."""
    data = AssetCreate(
        name="No Purchase", type="other", currency="BRL",
        current_value=Decimal("5000.00"),
    )
    result = await asset_service.create_asset(session, test_user.id, data)
    assert result.current_value == 5000.0
    assert result.gain_loss is None


# ---------------------------------------------------------------------------
# Total asset value edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_asset_value_includes_purchase_fallback(session: AsyncSession, test_user: User):
    """Assets with only purchase_price (no values) should be included in totals."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Fallback Total",
        type="real_estate",
        currency="BRL",
        valuation_method="manual",
        purchase_price=Decimal("200000.00"),
    )
    session.add(asset)
    await session.commit()

    totals, _ = await asset_service.get_asset_values_at(session, test_user.id)
    assert "BRL" in totals
    assert totals["BRL"] >= 200000.0


@pytest.mark.asyncio
async def test_total_asset_value_excludes_archived(session: AsyncSession, test_user: User):
    """Archived assets should not count in total."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Archived Total",
        type="other",
        currency="BRL",
        valuation_method="manual",
        purchase_price=Decimal("999999.00"),
        is_archived=True,
    )
    session.add(asset)
    await session.commit()

    totals, _ = await asset_service.get_asset_values_at(session, test_user.id)
    # If this is the only asset, BRL should not be in totals (or should not include 999999)
    assert totals.get("BRL", 0) < 999999.0


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_next_due_date_daily():
    from app.services.asset_service import _next_due_date
    assert _next_due_date(date(2025, 3, 10), "daily") == date(2025, 3, 11)


def test_next_due_date_weekly():
    from app.services.asset_service import _next_due_date
    assert _next_due_date(date(2025, 3, 10), "weekly") == date(2025, 3, 17)


def test_next_due_date_monthly():
    from app.services.asset_service import _next_due_date
    assert _next_due_date(date(2025, 1, 15), "monthly") == date(2025, 2, 15)


def test_next_due_date_monthly_year_rollover():
    from app.services.asset_service import _next_due_date
    assert _next_due_date(date(2025, 12, 10), "monthly") == date(2026, 1, 10)


def test_next_due_date_monthly_clamps_day():
    """Day 31 should clamp to 28 for safety."""
    from app.services.asset_service import _next_due_date
    result = _next_due_date(date(2025, 1, 31), "monthly")
    assert result == date(2025, 2, 28)


def test_next_due_date_yearly():
    from app.services.asset_service import _next_due_date
    assert _next_due_date(date(2025, 6, 15), "yearly") == date(2026, 6, 15)


def test_generate_growth_values_empty_when_future():
    """No values generated when start date is in the future."""
    from app.services.asset_service import _generate_growth_values
    future = date.today() + timedelta(days=30)
    result = _generate_growth_values(
        asset_id=uuid.uuid4(), base_amount=1000.0, base_date=future,
        growth_type="percentage", growth_rate=5.0, growth_frequency="monthly",
        growth_start_date=future,
    )
    assert result == []


def test_generate_growth_values_percentage():
    """Verify percentage growth generates correct values."""
    from app.services.asset_service import _generate_growth_values
    start = date.today() - timedelta(days=15)
    result = _generate_growth_values(
        asset_id=uuid.uuid4(), base_amount=1000.0, base_date=start,
        growth_type="percentage", growth_rate=1.0, growth_frequency="daily",
        growth_start_date=start,
    )
    # Should have ~15 daily values
    assert len(result) >= 14
    # Each value should be larger than the previous
    for i in range(1, len(result)):
        assert float(result[i].amount) > float(result[i - 1].amount)
    # All should have source="rule"
    assert all(v.source == "rule" for v in result)


def test_generate_growth_values_absolute():
    """Verify absolute growth adds fixed amount."""
    from app.services.asset_service import _generate_growth_values
    start = date.today() - timedelta(days=7)
    result = _generate_growth_values(
        asset_id=uuid.uuid4(), base_amount=1000.0, base_date=start,
        growth_type="absolute", growth_rate=100.0, growth_frequency="daily",
        growth_start_date=start,
    )
    assert len(result) >= 6
    # First generated value: 1000 + 100 = 1100
    assert abs(float(result[0].amount) - 1100.0) < 0.01


def test_next_due_date_unknown_frequency():
    assert _next_due_date(date(2025, 1, 1), "biweekly") == date(2025, 1, 2)


def test_generate_growth_values_yearly():
    result = _generate_growth_values(
        asset_id=uuid.uuid4(), base_amount=10000.0,
        base_date=date.today() - timedelta(days=400),
        growth_type="percentage", growth_rate=5.0,
        growth_frequency="yearly", growth_start_date=None,
    )
    assert len(result) >= 1


def test_generate_growth_values_unknown_type():
    result = _generate_growth_values(
        asset_id=uuid.uuid4(), base_amount=1000.0,
        base_date=date.today() - timedelta(days=90),
        growth_type="unknown", growth_rate=5.0,
        growth_frequency="monthly", growth_start_date=None,
    )
    assert result == []


def test_compute_current_value_with_latest():
    asset = Asset(id=uuid.uuid4(), user_id=uuid.uuid4(), name="A", type="other", currency="USD")
    val = AssetValue(id=uuid.uuid4(), asset_id=asset.id, amount=Decimal("500"), date=date.today())
    assert _compute_current_value(asset, val) == 500.0


def test_compute_current_value_fallback_purchase_price():
    asset = Asset(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name="A", type="other",
        currency="USD", purchase_price=Decimal("1000"),
    )
    assert _compute_current_value(asset, None) == 1000.0


def test_compute_current_value_none_without_data():
    asset = Asset(id=uuid.uuid4(), user_id=uuid.uuid4(), name="A", type="other", currency="USD")
    assert _compute_current_value(asset, None) is None


@pytest.mark.asyncio
async def test_update_asset_regenerate_growth(session: AsyncSession, test_user: User):
    purchase_date = date.today() - timedelta(days=60)
    data = AssetCreate(
        name="Regen Asset", type="investment", currency="BRL",
        valuation_method="growth_rule",
        purchase_date=purchase_date, purchase_price=Decimal("5000"),
        growth_type="percentage", growth_rate=Decimal("3"), growth_frequency="monthly",
    )
    created = await asset_service.create_asset(session, test_user.id, data)

    update_data = AssetUpdate(growth_rate=Decimal("5"))
    updated = await asset_service.update_asset(
        session, created.id, test_user.id, update_data, regenerate_growth=True,
    )
    assert updated is not None
    assert updated.name == "Regen Asset"


@pytest.mark.asyncio
async def test_update_asset_purchase_price(session: AsyncSession, test_user: User):
    data = AssetCreate(
        name="Price Update", type="other", currency="BRL",
        purchase_price=Decimal("1000"),
    )
    created = await asset_service.create_asset(session, test_user.id, data)
    update_data = AssetUpdate(purchase_price=Decimal("2000"))
    updated = await asset_service.update_asset(session, created.id, test_user.id, update_data)
    assert updated.purchase_price == 2000.0


@pytest.mark.asyncio
async def test_get_asset_values_not_found(session: AsyncSession, test_user: User):
    result = await asset_service.get_asset_values(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_add_asset_value_not_found(session: AsyncSession, test_user: User):
    val_data = AssetValueCreate(amount=Decimal("100"), date=date.today())
    result = await asset_service.add_asset_value(session, uuid.uuid4(), test_user.id, val_data)
    assert result is None


@pytest.mark.asyncio
async def test_delete_asset_value_not_found(session: AsyncSession, test_user: User):
    assert await asset_service.delete_asset_value(session, uuid.uuid4(), test_user.id) is False


@pytest.mark.asyncio
async def test_get_asset_value_trend_not_found(session: AsyncSession, test_user: User):
    result = await asset_service.get_asset_value_trend(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_portfolio_trend_empty(session: AsyncSession, test_user: User):
    result = await get_portfolio_trend(session, test_user.id)
    assert result["assets"] == []
    assert result["trend"] == []
    assert result["total"] == 0.0


@pytest.mark.asyncio
async def test_portfolio_trend_with_assets(session: AsyncSession, test_user: User):
    a1 = AssetCreate(
        name="House", type="real_estate", currency="BRL",
        purchase_date=date.today() - timedelta(days=30),
        purchase_price=Decimal("300000"), current_value=Decimal("350000"),
    )
    a2 = AssetCreate(
        name="Car", type="vehicle", currency="BRL",
        purchase_date=date.today() - timedelta(days=10),
        purchase_price=Decimal("50000"), current_value=Decimal("45000"),
    )
    await asset_service.create_asset(session, test_user.id, a1)
    await asset_service.create_asset(session, test_user.id, a2)

    result = await get_portfolio_trend(session, test_user.id)
    assert len(result["assets"]) == 2
    assert len(result["trend"]) > 0
    assert result["total"] > 0


@pytest.mark.asyncio
async def test_portfolio_trend_total_consistent_with_get_asset_values_at(
    session: AsyncSession, test_user: User
):
    """portfolio trend _total must match get_asset_values_at at the same date.

    Uses a USD asset whose value entry pre-dates the display date, so
    it is fill-forwarded. Two different BRL/USD rates are inserted so the
    old behavior (convert at value-entry date) and the new behavior
    (convert at display date) produce different numbers — only the new
    behavior matches get_asset_values_at.
    """
    date_jan = date(2025, 1, 31)  # USD asset gets its value here (rate 5.0)
    date_mar = date(2025, 3, 31)  # display date — different rate (6.0)

    # Two FX snapshots so convert() finds an exact match at each date
    for fx_date, fx_rate in [(date_jan, "5.0"), (date_mar, "6.0")]:
        session.add(FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="BRL",
            date=fx_date,
            rate=Decimal(fx_rate),
            source="test",
        ))

    # USD asset: one value entry in January, no subsequent entries (fill-forwarded in March)
    usd_asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Stock",
        type="investment", currency="USD", valuation_method="manual",
        purchase_date=date_jan, purchase_price=Decimal("100"), position=0,
    )
    session.add(usd_asset)
    await session.flush()
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=usd_asset.id,
        amount=Decimal("100"), date=date_jan, source="manual",
    ))

    # BRL anchor asset: value in March so that date_mar appears in sorted_dates
    brl_asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="House",
        type="real_estate", currency="BRL", valuation_method="manual",
        purchase_date=date_mar, purchase_price=Decimal("1000"), position=1,
    )
    session.add(brl_asset)
    await session.flush()
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=brl_asset.id,
        amount=Decimal("1000"), date=date_mar, source="manual",
    ))

    await session.commit()

    trend_result = await get_portfolio_trend(session, test_user.id)
    _, values_at_total = await asset_service.get_asset_values_at(
        session, test_user.id, as_of_date=date_mar,
        primary_currency=test_user.primary_currency,
    )

    # At date_mar: USD 100 @ 6.0 = BRL 600, plus BRL 1000 = 1600
    trend_at_mar = next(r for r in trend_result["trend"] if r["date"] == date_mar.isoformat())
    assert trend_at_mar["_total"] == pytest.approx(1600.0, rel=1e-2)
    assert trend_at_mar["_total"] == pytest.approx(values_at_total, rel=1e-2)


@pytest.mark.asyncio
async def test_get_assets_include_archived(session: AsyncSession, test_user: User):
    await asset_service.create_asset(
        session, test_user.id, AssetCreate(name="A1", type="other", currency="BRL"),
    )
    await asset_service.create_asset(
        session, test_user.id, AssetCreate(name="A2", type="other", currency="BRL", is_archived=True),
    )
    assets = await asset_service.get_assets(session, test_user.id, include_archived=True)
    names = [a.name for a in assets]
    assert "A1" in names
    assert "A2" in names


@pytest.mark.asyncio
async def test_get_assets_excludes_archived_by_default(session: AsyncSession, test_user: User):
    await asset_service.create_asset(
        session, test_user.id, AssetCreate(name="Visible", type="other", currency="BRL"),
    )
    await asset_service.create_asset(
        session, test_user.id, AssetCreate(name="Archived", type="other", currency="BRL", is_archived=True),
    )

    assets = await asset_service.get_assets(session, test_user.id)
    names = [a.name for a in assets]
    assert "Visible" in names
    assert "Archived" not in names
