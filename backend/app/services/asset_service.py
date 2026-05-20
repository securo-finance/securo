import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
    get_market_price_provider,
)
from app.schemas.asset import AssetCreate, AssetUpdate, AssetValueCreate, AssetRead, AssetValueRead
from app.services.fx_rate_service import convert, stamp_primary_amount

logger = logging.getLogger(__name__)


def _next_due_date(last_date: date, frequency: str) -> date:
    """Calculate the next due date based on frequency."""
    if frequency == "daily":
        return last_date + timedelta(days=1)
    elif frequency == "weekly":
        return last_date + timedelta(weeks=1)
    elif frequency == "monthly":
        month = last_date.month + 1
        year = last_date.year
        if month > 12:
            month = 1
            year += 1
        day = min(last_date.day, 28)
        return date(year, month, day)
    elif frequency == "yearly":
        return date(last_date.year + 1, last_date.month, last_date.day)
    return last_date + timedelta(days=1)


def _compute_current_value(asset: Asset, latest_value: Optional[AssetValue]) -> Optional[float]:
    """Compute the current value of an asset from its latest AssetValue.
    Falls back to purchase_price if no value entries exist yet."""
    # Market-priced assets are authoritative on (units × last_price). The
    # AssetValue history exists for the chart, but the "live" number users
    # see should reflect the most recent quote even between scheduled syncs.
    if asset.valuation_method == "market_price":
        if asset.last_price is not None and asset.units is not None:
            return float(Decimal(str(asset.last_price)) * Decimal(str(asset.units)))
        if latest_value is not None:
            return float(latest_value.amount)
        return None
    if latest_value is None:
        if asset.purchase_price is not None:
            return float(asset.purchase_price)
        return None
    return float(latest_value.amount)


def _generate_growth_values(
    asset_id: uuid.UUID,
    base_amount: float,
    base_date: date,
    growth_type: str,
    growth_rate: float,
    growth_frequency: str,
    growth_start_date: Optional[date],
) -> list[AssetValue]:
    """Generate all AssetValue rows from base_date to today using the growth rule.
    When growth_start_date is set, growth iteration begins from that date — not
    from base_date — so the asset accrues no growth for the gap between
    purchase and the configured growth start."""
    today = date.today()
    if growth_start_date and today < growth_start_date:
        return []

    values: list[AssetValue] = []
    current_amount = base_amount
    # Match the frontend preview: `growth_start_date or base_date`. Otherwise
    # backfill applied growth periods between purchase_date and
    # growth_start_date that the form said wouldn't accrue, leaving the
    # list-page total exactly N growth periods higher than the edit
    # dialog's calculated value.
    current_date = growth_start_date if growth_start_date else base_date

    while True:
        next_due = _next_due_date(current_date, growth_frequency)
        if next_due > today:
            break
        if growth_type == "percentage":
            current_amount = current_amount * (1 + growth_rate / 100)
        elif growth_type == "absolute":
            current_amount = current_amount + growth_rate
        else:
            break
        values.append(AssetValue(
            asset_id=asset_id,
            amount=Decimal(str(round(current_amount, 6))),
            date=next_due,
            source="rule",
        ))
        current_date = next_due
        if len(values) >= 10000:
            break

    return values


def _asset_to_read(asset: Asset, latest_value: Optional[AssetValue], value_count: int) -> AssetRead:
    """Convert an Asset model + computed fields to AssetRead schema."""
    current_value = _compute_current_value(asset, latest_value)
    gain_loss = None
    if current_value is not None and asset.purchase_price is not None:
        gain_loss = current_value - float(asset.purchase_price)

    return AssetRead(
        id=asset.id,
        user_id=asset.user_id,
        name=asset.name,
        type=asset.type,
        currency=asset.currency,
        units=float(asset.units) if asset.units is not None else None,
        valuation_method=asset.valuation_method,
        purchase_date=asset.purchase_date,
        purchase_price=float(asset.purchase_price) if asset.purchase_price is not None else None,
        sell_date=asset.sell_date,
        sell_price=float(asset.sell_price) if asset.sell_price is not None else None,
        growth_type=asset.growth_type,
        growth_rate=float(asset.growth_rate) if asset.growth_rate is not None else None,
        growth_frequency=asset.growth_frequency,
        growth_start_date=asset.growth_start_date,
        is_archived=asset.is_archived,
        position=asset.position,
        current_value=current_value,
        gain_loss=gain_loss,
        value_count=value_count,
        source=asset.source,
        connection_id=asset.connection_id,
        isin=asset.isin,
        maturity_date=asset.maturity_date,
        group_id=asset.group_id,
        ticker=asset.ticker,
        ticker_exchange=asset.ticker_exchange,
        last_price=float(asset.last_price) if asset.last_price is not None else None,
        last_price_at=asset.last_price_at,
        logo_url=asset.logo_url,
    )


async def _get_latest_value(session: AsyncSession, asset_id: uuid.UUID) -> Optional[AssetValue]:
    """Get the most recent AssetValue for an asset."""
    result = await session.execute(
        select(AssetValue)
        .where(AssetValue.asset_id == asset_id)
        .order_by(desc(AssetValue.date), desc(AssetValue.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_value_as_of(
    session: AsyncSession, asset_id: uuid.UUID, as_of_date: date
) -> Optional[AssetValue]:
    """Get the most recent AssetValue for an asset on or before as_of_date."""
    result = await session.execute(
        select(AssetValue)
        .where(AssetValue.asset_id == asset_id, AssetValue.date <= as_of_date)
        .order_by(desc(AssetValue.date), desc(AssetValue.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_asset_native_values(
    session: AsyncSession,
    assets: list[Asset],
    up_to_date: Optional[date] = None,
) -> dict[str, list[tuple[date, float]]]:
    """Bulk-load AssetValue rows for all assets in one query.

    Returns {aid: [(date, amount), ...]} sorted ascending by (date, id).
    When purchase_price and purchase_date are both set and purchase_date
    predates the first recorded value, it is prepended as the earliest anchor.
    """
    if not assets:
        return {}

    asset_ids = [a.id for a in assets]
    q = (
        select(AssetValue.asset_id, AssetValue.date, AssetValue.amount)
        .where(AssetValue.asset_id.in_(asset_ids))
        .order_by(AssetValue.asset_id, AssetValue.date, AssetValue.id)
    )
    if up_to_date is not None:
        q = q.where(AssetValue.date <= up_to_date)

    rows = (await session.execute(q)).all()

    values_map: dict[str, list[tuple[date, float]]] = {str(a.id): [] for a in assets}
    for aid, d, amt in rows:
        values_map[str(aid)].append((d, float(amt)))

    for asset in assets:
        aid = str(asset.id)
        vals = values_map[aid]
        if asset.purchase_price is not None and asset.purchase_date is not None:
            if not vals or asset.purchase_date < vals[0][0]:
                vals.insert(0, (asset.purchase_date, float(asset.purchase_price)))

    return values_map


def _fill_forward_at(
    asset: Asset,
    sorted_vals: list[tuple[date, float]],
    as_of: date,
) -> Optional[float]:
    """Return the fill-forwarded native value of asset at as_of, or None.

    Scans sorted_vals for the latest entry on or before as_of. Falls back
    to purchase_price when purchase_date is None (asset predates any known
    date) and no value history is available for the requested date.
    """
    result = None
    for d, v in sorted_vals:
        if d <= as_of:
            result = v
        else:
            break
    if result is None and asset.purchase_price is not None and asset.purchase_date is None:
        result = float(asset.purchase_price)
    return result


async def _get_value_count(session: AsyncSession, asset_id: uuid.UUID) -> int:
    """Get the number of AssetValue entries for an asset."""
    result = await session.scalar(
        select(func.count()).select_from(AssetValue).where(AssetValue.asset_id == asset_id)
    )
    return result or 0


async def get_assets(
    session: AsyncSession, user_id: uuid.UUID, include_archived: bool = False
) -> list[AssetRead]:
    """List all assets for a user with computed current_value."""
    query = select(Asset).where(Asset.user_id == user_id)
    if not include_archived:
        query = query.where(Asset.is_archived == False)
    query = query.order_by(Asset.position, Asset.name)

    result = await session.execute(query)
    assets = list(result.scalars().all())

    reads = []
    for asset in assets:
        latest = await _get_latest_value(session, asset.id)
        count = await _get_value_count(session, asset.id)
        reads.append(_asset_to_read(asset, latest, count))
    return reads


async def get_asset(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[AssetRead]:
    """Get a single asset with computed fields."""
    result = await session.execute(
        select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return None
    latest = await _get_latest_value(session, asset.id)
    count = await _get_value_count(session, asset.id)
    return _asset_to_read(asset, latest, count)


async def create_asset(
    session: AsyncSession,
    user_id: uuid.UUID,
    data: AssetCreate,
    *,
    market_provider: Optional[MarketPriceProvider] = None,
) -> AssetRead:
    """Create an asset, optionally with an initial value."""
    # Market-priced path: fetch a live quote first so we can derive currency
    # and the initial value from the ticker. Validate up-front rather than
    # half-creating an asset and failing on a 5xx from Yahoo.
    quote = None
    if data.valuation_method == "market_price":
        if not data.ticker:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ticker is required for market_price assets",
            )
        if data.units is None or data.units <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="units (quantity) must be > 0 for market_price assets",
            )
        provider = market_provider or get_market_price_provider()
        quote = await provider.get_quote(data.ticker)
        if quote is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not fetch quote for {data.ticker}",
            )

    asset = Asset(
        user_id=user_id,
        name=data.name,
        type=data.type,
        # For market_price, the quote's currency is authoritative — a user
        # entering PETR4.SA from an English-language form shouldn't end up
        # with USD just because the dropdown defaulted to USD.
        currency=quote.currency if quote else data.currency,
        units=data.units,
        valuation_method=data.valuation_method,
        purchase_date=data.purchase_date,
        purchase_price=data.purchase_price,
        sell_date=data.sell_date,
        sell_price=data.sell_price,
        growth_type=data.growth_type,
        growth_rate=data.growth_rate,
        growth_frequency=data.growth_frequency,
        growth_start_date=data.growth_start_date,
        is_archived=data.is_archived,
        position=data.position,
        group_id=data.group_id,
        ticker=data.ticker.upper() if data.ticker else None,
        ticker_exchange=data.ticker_exchange or (quote.exchange if quote else None),
        last_price=Decimal(str(quote.price)) if quote else None,
        last_price_at=datetime.now(timezone.utc) if quote else None,
        logo_url=quote.logo_url if quote else None,
        source="yfinance" if data.valuation_method == "market_price" else "manual",
    )
    session.add(asset)
    await session.flush()

    # Seed the first AssetValue from the live quote so the portfolio chart
    # has a starting data point without waiting for the scheduled refresh.
    if data.valuation_method == "market_price" and quote is not None:
        initial_amount = Decimal(str(quote.price)) * Decimal(str(data.units))
        session.add(
            AssetValue(
                asset_id=asset.id,
                amount=initial_amount,
                date=date.today(),
                source="sync",
            )
        )

    # Create initial value if provided
    if data.current_value is not None:
        value = AssetValue(
            asset_id=asset.id,
            amount=data.current_value,
            date=date.today(),
            source="manual",
        )
        session.add(value)
    elif data.valuation_method == "growth_rule" and data.purchase_price is not None:
        # Seed the initial value from purchase price
        base_date = data.purchase_date or data.growth_start_date or date.today()
        seed = AssetValue(
            asset_id=asset.id,
            amount=data.purchase_price,
            date=base_date,
            source="manual",
        )
        session.add(seed)

        # Backfill all growth values from the seed date to today
        if data.growth_type and data.growth_rate and data.growth_frequency:
            backfill = _generate_growth_values(
                asset_id=asset.id,
                base_amount=float(data.purchase_price),
                base_date=base_date,
                growth_type=data.growth_type,
                growth_rate=float(data.growth_rate),
                growth_frequency=data.growth_frequency,
                growth_start_date=data.growth_start_date,
            )
            for v in backfill:
                session.add(v)

    # Stamp purchase_price_primary
    if asset.purchase_price is not None:
        await stamp_primary_amount(
            session, user_id, asset,
            amount_field="purchase_price",
            primary_field="purchase_price_primary",
            rate_field="_no_rate",  # Asset has no rate field
            date_field="purchase_date",
        )

    await session.commit()
    await session.refresh(asset)
    latest = await _get_latest_value(session, asset.id)
    count = await _get_value_count(session, asset.id)
    return _asset_to_read(asset, latest, count)


async def update_asset(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID, data: AssetUpdate,
    regenerate_growth: bool = False,
) -> Optional[AssetRead]:
    """Partial update of an asset."""
    result = await session.execute(
        select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return None

    update_data = data.model_dump(exclude_unset=True)
    # Prevent changing valuation_method on existing assets
    update_data.pop("valuation_method", None)
    for key, value in update_data.items():
        setattr(asset, key, value)

    # Regenerate growth-rule values if requested
    if regenerate_growth and asset.valuation_method == "growth_rule":
        # Delete all rule-generated values
        await session.execute(
            select(AssetValue)
            .where(AssetValue.asset_id == asset.id, AssetValue.source == "rule")
        )
        from sqlalchemy import delete as sa_delete
        await session.execute(
            sa_delete(AssetValue).where(
                AssetValue.asset_id == asset.id,
                AssetValue.source == "rule",
            )
        )
        # Regenerate from purchase_price
        if asset.purchase_price and asset.growth_type and asset.growth_rate and asset.growth_frequency:
            base_date = asset.purchase_date or asset.growth_start_date or date.today()
            backfill = _generate_growth_values(
                asset_id=asset.id,
                base_amount=float(asset.purchase_price),
                base_date=base_date,
                growth_type=asset.growth_type,
                growth_rate=float(asset.growth_rate),
                growth_frequency=asset.growth_frequency,
                growth_start_date=asset.growth_start_date,
            )
            for v in backfill:
                session.add(v)

    # Re-stamp purchase_price_primary if purchase_price or currency changed
    if "purchase_price" in update_data or "currency" in update_data:
        if asset.purchase_price is not None:
            await stamp_primary_amount(
                session, user_id, asset,
                amount_field="purchase_price",
                primary_field="purchase_price_primary",
                rate_field="_no_rate",
                date_field="purchase_date",
            )

    # If units change on a market-priced asset, rewrite today's AssetValue with
    # the new (units × last_price). Without this, the portfolio chart keeps
    # plotting the old position size even though the header and wallet totals
    # (computed live) already reflect the new units — the two disagree until
    # the next scheduled refresh overwrites today's row.
    if (
        "units" in update_data
        and asset.valuation_method == "market_price"
        and asset.last_price is not None
        and asset.units is not None
        and asset.units > 0
    ):
        await _apply_price_to_asset(session, asset, Decimal(str(asset.last_price)))

    await session.commit()
    await session.refresh(asset)
    latest = await _get_latest_value(session, asset.id)
    count = await _get_value_count(session, asset.id)
    return _asset_to_read(asset, latest, count)


async def delete_asset(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Delete an asset (cascades to values)."""
    result = await session.execute(
        select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return False
    await session.delete(asset)
    await session.commit()
    return True


async def get_asset_values(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[list[AssetValueRead]]:
    """Get value history for an asset, most recent first."""
    # Verify ownership
    owner_check = await session.execute(
        select(Asset.id).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    if not owner_check.scalar_one_or_none():
        return None

    result = await session.execute(
        select(AssetValue)
        .where(AssetValue.asset_id == asset_id)
        .order_by(desc(AssetValue.date), desc(AssetValue.id))
    )
    values = result.scalars().all()
    return [AssetValueRead.model_validate(v) for v in values]


async def add_asset_value(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID, data: AssetValueCreate
) -> Optional[AssetValueRead]:
    """Add a new value entry for an asset."""
    # Verify ownership
    owner_check = await session.execute(
        select(Asset.id).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    if not owner_check.scalar_one_or_none():
        return None

    value = AssetValue(
        asset_id=asset_id,
        amount=data.amount,
        date=data.date,
        source="manual",
    )
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return AssetValueRead.model_validate(value)


async def delete_asset_value(
    session: AsyncSession, value_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Delete a specific asset value entry."""
    result = await session.execute(
        select(AssetValue)
        .join(Asset, AssetValue.asset_id == Asset.id)
        .where(AssetValue.id == value_id, Asset.user_id == user_id)
    )
    value = result.scalar_one_or_none()
    if not value:
        return False
    await session.delete(value)
    await session.commit()
    return True


async def get_asset_value_trend(
    session: AsyncSession, asset_id: uuid.UUID, user_id: uuid.UUID, months: int = 12
) -> Optional[list[dict]]:
    """Get value trend data for charting."""
    # Verify ownership
    owner_check = await session.execute(
        select(Asset.id).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    if not owner_check.scalar_one_or_none():
        return None

    result = await session.execute(
        select(AssetValue.date, AssetValue.amount)
        .where(AssetValue.asset_id == asset_id)
        .order_by(AssetValue.date)
    )
    rows = result.all()
    return [{"date": row[0].isoformat(), "amount": float(row[1])} for row in rows]


async def get_portfolio_trend(
    session: AsyncSession, user_id: uuid.UUID
) -> dict:
    """Get portfolio trend data for stacked area chart.
    Returns asset metadata + pivoted trend with fill-forward values.
    Sold assets are included so their pre-sell history still contributes
    to the historical total; their contribution drops to 0 the day after
    sell_date."""
    result = await session.execute(
        select(Asset).where(
            Asset.user_id == user_id,
            Asset.is_archived == False,
        ).order_by(Asset.position, Asset.name)
    )
    active_assets = list(result.scalars().all())

    if not active_assets:
        return {"assets": [], "trend": [], "total": 0.0}

    # Get user's primary currency for conversion
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    values_map = await _load_asset_native_values(session, active_assets)

    asset_meta = []
    asset_currency: dict[str, str] = {}
    sell_date_by_aid: dict[str, date] = {}
    all_dates: set[date] = set()

    for asset in active_assets:
        aid = str(asset.id)
        asset_meta.append({
            "id": aid,
            "name": asset.name,
            "type": asset.type,
            "group_id": str(asset.group_id) if asset.group_id else None,
        })
        asset_currency[aid] = asset.currency

        vals = values_map[aid]

        # If the asset was sold and a sell_price is recorded, treat it as the
        # asset's terminal value on sell_date so the chart reflects the
        # realized value before dropping to 0.
        if asset.sell_date is not None:
            sell_date_by_aid[aid] = asset.sell_date
            if asset.sell_price is not None:
                vals = [(d, v) for d, v in vals if d <= asset.sell_date]
                if not vals or vals[-1][0] != asset.sell_date:
                    vals.append((asset.sell_date, float(asset.sell_price)))
                values_map[aid] = vals

        for d, _ in vals:
            all_dates.add(d)

    if not all_dates:
        return {"assets": asset_meta, "trend": [], "total": 0.0}

    sorted_dates = sorted(all_dates)

    # Build lookup: aid -> {date: value}
    value_lookup: dict[str, dict[date, float]] = {}
    first_date: dict[str, date] = {}
    for aid in [a["id"] for a in asset_meta]:
        value_lookup[aid] = dict(values_map[aid])
        if values_map[aid]:
            first_date[aid] = values_map[aid][0][0]

    # Build trend with fill-forward; 0 before first date (for stacking).
    # Each native-currency amount is converted at the display date `d` so that
    # fill-forwarded values reflect current FX rates — consistent with
    # get_asset_values_at(as_of_date=d).
    trend = []
    last_known: dict[str, float] = {}  # native currency amounts
    for aid in [a["id"] for a in asset_meta]:
        last_known[aid] = 0.0

    for d in sorted_dates:
        row: dict[str, object] = {"date": d.isoformat()}
        date_total = 0.0
        for aid in [a["id"] for a in asset_meta]:
            if d in value_lookup[aid]:
                last_known[aid] = value_lookup[aid][d]
            # Use 0 before asset exists (stacking needs numeric values)
            if aid in first_date and d >= first_date[aid]:
                native = last_known[aid]
            else:
                native = 0.0
            # After sell_date, the asset has been liquidated — drop to 0 so
            # it stops contributing to the portfolio total going forward.
            if aid in sell_date_by_aid and d > sell_date_by_aid[aid]:
                native = 0.0
                last_known[aid] = 0.0

            # Convert native amount to primary currency at this display date
            currency = asset_currency[aid]
            if native != 0.0 and currency != primary_currency:
                converted, _ = await convert(
                    session, Decimal(str(native)), currency, primary_currency, d
                )
                val = round(float(converted), 2)
            else:
                val = round(native, 2)

            row[aid] = val
            date_total += val
        row["_total"] = round(date_total, 2)
        trend.append(row)

    # The header total matches the last row's _total — both use the same
    # per-display-date conversion so no second conversion is needed.
    total = trend[-1]["_total"] if trend else 0.0

    return {"assets": asset_meta, "trend": trend, "total": round(total, 2)}


async def get_asset_values_at(
    session: AsyncSession,
    user_id: uuid.UUID,
    as_of_date: Optional[date] = None,
    primary_currency: Optional[str] = None,
) -> tuple[dict[str, float], float]:
    """Return (per_currency_totals, primary_total) for all active assets.

    - as_of_date=None: uses live prices (current view).
    - as_of_date set: uses the latest AssetValue on or before that date,
      falling back to purchase_price only if the asset existed by that date.
    - primary_currency=None: primary_total is 0.0.
    """
    result = await session.execute(
        select(Asset).where(
            Asset.user_id == user_id,
            Asset.is_archived == False,
            Asset.sell_date.is_(None),
        )
    )
    assets = list(result.scalars().all())

    totals: dict[str, float] = {}
    primary_total = 0.0

    if as_of_date is not None:
        values_map = await _load_asset_native_values(session, assets, up_to_date=as_of_date)

    for asset in assets:
        if as_of_date is not None:
            amount: Optional[float] = _fill_forward_at(asset, values_map[str(asset.id)], as_of_date)
        else:
            latest = await _get_latest_value(session, asset.id)
            amount = _compute_current_value(asset, latest)

        if not amount:
            continue

        totals[asset.currency] = totals.get(asset.currency, 0.0) + amount

        if primary_currency is not None:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, as_of_date
            )
            primary_total += float(converted)

    return totals, primary_total


# ============================================================================
# Market-price refresh
# ============================================================================


async def _apply_price_to_asset(
    session: AsyncSession, asset: Asset, new_price: Decimal
) -> None:
    """Update the cached price and upsert today's AssetValue.

    Shared by the single-asset and batch refresh paths so both behave
    identically: price + timestamp get stamped; today's value gets
    inserted or overwritten so running the task multiple times per day
    doesn't pile up duplicate rows.
    """
    asset.last_price = new_price
    asset.last_price_at = datetime.now(timezone.utc)

    if not asset.units or asset.units <= 0:
        return

    today = date.today()
    new_amount = new_price * Decimal(str(asset.units))
    existing = await session.execute(
        select(AssetValue)
        .where(AssetValue.asset_id == asset.id, AssetValue.date == today)
        .order_by(desc(AssetValue.id))
        .limit(1)
    )
    today_value = existing.scalar_one_or_none()
    if today_value is not None:
        today_value.amount = new_amount
        today_value.source = "sync"
    else:
        session.add(
            AssetValue(
                asset_id=asset.id,
                amount=new_amount,
                date=today,
                source="sync",
            )
        )


async def refresh_market_price_asset(
    session: AsyncSession,
    asset: Asset,
    *,
    market_provider: Optional[MarketPriceProvider] = None,
) -> bool:
    """Re-quote a single market-priced asset and update its cached price.

    Returns True when a new quote was persisted, False otherwise (no quote
    available, stale price unchanged, or missing fields).
    """
    if asset.valuation_method != "market_price" or not asset.ticker:
        return False

    provider = market_provider or get_market_price_provider()
    try:
        quote = await provider.get_quote(asset.ticker)
    except MarketPriceRateLimitedError:
        # Let the scheduler see this explicitly so it can back off globally.
        raise
    except Exception as e:
        logger.warning("Market price refresh failed for %s: %s", asset.ticker, e)
        return False

    if quote is None or quote.price is None:
        return False

    await _apply_price_to_asset(session, asset, Decimal(str(quote.price)))
    # Opportunistic logo backfill: assets created before Brandfetch was
    # configured have no logo_url. On the next single-asset refresh (which
    # goes through the full get_quote → website lookup), stamp it in.
    # The batch refresh path doesn't have website info so it leaves logos
    # alone; manual refreshes and creates cover the fill-in.
    if not asset.logo_url and quote.logo_url:
        asset.logo_url = quote.logo_url
    await session.flush()
    return True


async def refresh_all_market_prices(
    session: AsyncSession,
    *,
    market_provider: Optional[MarketPriceProvider] = None,
) -> dict[str, int]:
    """Refresh every non-archived market-priced asset in the database.

    Uses the provider's batch ``get_latest_prices`` endpoint when possible —
    one HTTP request covers the whole portfolio via ``yfinance.download``
    instead of one call per asset. Falls back silently to per-asset refresh
    if the batch returns nothing (provider without bulk support, or a hard
    failure on Yahoo's end).

    Returns a summary counting successes, skips, and rate-limit halts —
    surfaced as the Celery task's return payload for observability.
    """
    result = await session.execute(
        select(Asset).where(
            Asset.valuation_method == "market_price",
            Asset.is_archived == False,
            Asset.sell_date.is_(None),
            Asset.ticker.isnot(None),
        )
    )
    assets = list(result.scalars().all())

    if not assets:
        return {"refreshed": 0, "skipped": 0, "rate_limited": 0}

    provider = market_provider or get_market_price_provider()
    tickers = [a.ticker for a in assets if a.ticker]

    # Batch path: one request → dict[SYMBOL, price]. On rate-limit we halt
    # immediately — retrying within the same task would just pile on 429s
    # and risk an IP-level cookie ban.
    try:
        prices = await provider.get_latest_prices(tickers)
    except MarketPriceRateLimitedError:
        logger.warning("Yahoo rate-limited the batch fetch; skipping this cycle")
        return {"refreshed": 0, "skipped": len(assets), "rate_limited": 1}
    except Exception as e:
        logger.warning("Batch price fetch failed, falling back to per-asset: %s", e)
        prices = {}

    refreshed = 0
    skipped = 0

    for asset in assets:
        if not asset.ticker:
            skipped += 1
            continue
        price = prices.get(asset.ticker.upper()) if prices else None
        if price is None:
            # Per-asset fallback: the batch missed this symbol (delisted
            # ticker, one-off provider error, etc.). Try the full quote
            # path which also populates name/currency if needed.
            try:
                ok = await refresh_market_price_asset(
                    session, asset, market_provider=provider
                )
            except MarketPriceRateLimitedError:
                logger.warning(
                    "Yahoo rate-limited mid-refresh after %d assets; halting",
                    refreshed,
                )
                await session.commit()
                return {
                    "refreshed": refreshed,
                    "skipped": skipped + (len(assets) - refreshed - skipped),
                    "rate_limited": 1,
                }
            if ok:
                refreshed += 1
            else:
                skipped += 1
            continue

        await _apply_price_to_asset(session, asset, price)
        refreshed += 1

    await session.commit()
    return {"refreshed": refreshed, "skipped": skipped, "rate_limited": 0}
