"""Money put into the charted assets, for the "Invested" line of the Assets page."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.user import User
from app.services import asset_service
from app.services.portfolio_performance_service import _cash_flows


async def get_invested_capital(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    *,
    end_date: Optional[date] = None,
) -> list[dict]:
    """Cumulative net money put into the assets of the portfolio chart.

    "Invested" is what was bought, fees included, minus what sales brought
    in, each at its own day's exchange rate. It reuses the cash flows the
    Performance tab separates from market moves, so both agree on what
    counts as money in or out. Returns ``[{"date", "amount"}]`` ascending,
    one point per date on which the total changes.
    """
    user = await session.get(User, user_id) if user_id is not None else None
    primary = user.primary_currency if user else get_settings().default_currency

    trend = await asset_service.get_portfolio_trend(
        session,
        workspace_id,
        user_id,
        asset_group_ids=asset_group_ids,
        primary_currency=primary,
    )
    if not trend["trend"]:
        return []

    asset_ids = [uuid.UUID(str(item["id"])) for item in trend["assets"]]
    if not asset_ids:
        return []
    assets = list(
        (
            await session.execute(
                select(Asset).where(Asset.workspace_id == workspace_id, Asset.id.in_(asset_ids))
            )
        )
        .scalars()
        .all()
    )
    if not assets:
        return []

    earliest = date.fromisoformat(str(trend["trend"][0]["date"]))
    first_trade = (
        await session.execute(
            select(func.min(AssetTransaction.date)).where(
                AssetTransaction.asset_id.in_([asset.id for asset in assets])
            )
        )
    ).scalar_one_or_none()
    # Flows only count strictly after the start date.
    start = min(earliest, first_trade or earliest) - timedelta(days=1)
    # The chart draws a trade dated after today, so the line covers it too.
    latest = date.fromisoformat(str(trend["trend"][-1]["date"]))
    end = end_date or max(app_today(), latest)

    flows = await _cash_flows(session, assets, trend["trend"], primary, start, end)

    # The flows are floats, as _cash_flows keeps them; the sums here are exact
    # and only each emitted total is rounded, so no cents drift in.
    net: dict[date, Decimal] = {}

    def add(day: date, value: float) -> None:
        net[day] = net.get(day, Decimal("0")) + Decimal(str(value))

    for source in (flows.contributions, flows.opening):
        for day, value in source.items():
            add(day, value)
    for day, value in flows.withdrawals.items():
        add(day, -value)
    # ``flows.fees`` is part of the contributions already, so it is not added.
    for day, trades in flows.trades.items():
        for trade in trades:
            add(day, trade.amount)
            add(day, trade.fee)

    points: list[dict] = []
    running = Decimal("0")
    previous = Decimal("0")
    for day in sorted(net):
        running += net[day]
        # Rounded like the chart's own values, so an unchanged price shows no gain.
        amount = running.quantize(Decimal("0.01"))
        if amount == previous:
            continue
        previous = amount
        points.append({"date": day, "amount": float(amount) or 0.0})  # never -0.0
    return points
