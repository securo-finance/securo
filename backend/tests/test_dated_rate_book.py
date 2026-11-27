"""DatedRateBook resolves a long date range from one series read per currency."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.services.asset_service import get_portfolio_trend
from app.services.fx_rate_service import DatedRateBook, get_rate


def _rate(currency: str, day: date, value: str) -> FxRate:
    return FxRate(
        base_currency="USD", quote_currency=currency, date=day, rate=Decimal(value), source="test"
    )


@pytest.mark.asyncio
async def test_book_matches_get_rate_on_every_date(session: AsyncSession):
    start = date(2020, 1, 10)
    # Gaps on purpose: BRL weekly, EUR only in two months, nothing for JPY.
    session.add_all(
        [_rate("BRL", start + timedelta(days=7 * i), f"5.{i:02d}") for i in range(20)]
        + [_rate("EUR", date(2020, 2, 3), "0.90"), _rate("EUR", date(2020, 3, 16), "0.93")]
    )
    await session.commit()

    book = DatedRateBook(session, allow_fetch=False)
    pairs = [("USD", "BRL"), ("BRL", "USD"), ("EUR", "BRL"), ("brl", "eur"), ("JPY", "BRL")]
    for offset in range(-15, 170, 3):
        day = start + timedelta(days=offset)
        for source, quote in pairs:
            expected = await get_rate(session, source, quote, day, allow_fetch=False)
            assert await book.rate(source, quote, day) == expected, (source, quote, day)


@pytest.mark.asyncio
async def test_book_reads_a_future_date_at_todays_rate(session: AsyncSession):
    today = date.today()
    session.add_all([_rate("BRL", today - timedelta(days=3), "5.10"), _rate("BRL", today, "5.20")])
    await session.commit()

    book = DatedRateBook(session, allow_fetch=False)
    assert await book.rate("USD", "BRL", today + timedelta(days=30)) == Decimal("5.20")


@pytest.mark.asyncio
async def test_portfolio_trend_reads_fx_rates_once_per_currency(
    session: AsyncSession, test_user, test_workspace
):
    start = date.today() - timedelta(days=400)
    session.add_all(_rate("BRL", start + timedelta(days=i), "5.00") for i in range(401))
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="ETF", type="investment", currency="USD", valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(asset_id=asset.id, amount=Decimal(100 + i), date=start + timedelta(days=i))
        for i in range(0, 400, 5)
    )
    await session.commit()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        trend = await get_portfolio_trend(session, test_workspace.id, primary_currency="BRL")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(trend["trend"]) == 80
    assert trend["trend"][0]["_total"] == 500.0
    fx_reads = [s for s in statements if "fx_rates" in s.lower()]
    # has_rate for the currency plus one series read, not two or more per date.
    assert len(fx_reads) <= 3, f"{len(fx_reads)} fx_rates statements for one trend"
