from __future__ import annotations

import math
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.benchmark import BenchmarkHistoryPoint
from app.services.portfolio_performance_service import (
    _benchmark_shadow_portfolio,
    get_portfolio_performance_multi,
    money_weighted_return,
    money_weighted_returns,
    time_weighted_returns,
)

D0 = date(2025, 1, 1)


def day(offset: int) -> date:
    return D0 + timedelta(days=offset)


class MultiHistoryProvider:
    def __init__(self, points: dict[str, list[BenchmarkHistoryPoint]]) -> None:
        self.points = points

    async def history(self, provider_name, symbol, start_date, end_date):
        return self.points[symbol]


def test_money_weighted_return_without_flows_is_the_plain_change():
    assert money_weighted_return(day(0), 1000, [], day(365), 1100) == pytest.approx(10.0)
    assert money_weighted_return(day(0), 1000, [], day(30), 900) == pytest.approx(-10.0)


def test_money_weighted_return_rewards_buying_before_a_rise():
    """Halve, double the stake at the bottom, recover: flat time-weighted,
    a gain money-weighted, since most of the money caught the recovery."""
    values = [(day(0), 100.0), (day(15), 50.0 + 100.0), (day(30), 300.0)]
    flows = {day(15): 100.0}
    assert time_weighted_returns(values, flows)[-1][1] == pytest.approx(0.0)
    # -100 - 100 g^-0.5 + 300 g^-1 = 0  =>  g^-0.5 = (1 + sqrt(13)) / 6
    expected = (36 / (1 + math.sqrt(13)) ** 2 - 1) * 100
    assert money_weighted_returns(values, flows)[-1][1] == pytest.approx(expected)


def test_money_weighted_return_ignores_the_days_before_the_first_buy():
    late = money_weighted_return(day(0), 0, [(day(181), 1000)], day(365), 1100)
    assert late == pytest.approx(10.0)


def test_money_weighted_return_of_a_same_day_round_trip_is_the_ratio():
    result = money_weighted_return(day(0), 0, [(day(1), 1000), (day(1), -990)], day(5), 0)
    assert result == pytest.approx(-1.0)


def test_money_weighted_return_edges():
    assert money_weighted_return(day(0), 0, [], day(10), 0) == 0.0
    # Everything bought is gone.
    assert money_weighted_return(day(0), 0, [(day(1), 500)], day(10), 0) == -100.0
    # Money taken out that was never put in balances at no rate.
    assert money_weighted_return(day(0), 0, [(day(1), -500)], day(10), 0) is None


def test_money_weighted_series_stays_flat_after_selling_everything():
    values = [(day(0), 100.0), (day(10), 110.0), (day(20), 0.0), (day(40), 0.0)]
    flows = {day(20): -110.0}
    series = money_weighted_returns(values, flows)
    assert series[0] == (day(0), 0.0)
    assert series[-1][1] == pytest.approx(10.0)
    assert series[-1][1] == pytest.approx(series[-2][1])


def test_shadow_benchmark_gets_the_portfolios_money_on_the_same_days():
    history = [(day(0), 100.0), (day(15), 50.0), (day(30), 100.0)]
    values, moved = _benchmark_shadow_portfolio(
        history, day(0), day(30), 100.0, {day(15): 100.0}, set()
    )
    assert values == [(day(0), 100.0), (day(15), 150.0), (day(30), 300.0)]
    assert moved == {day(15): 100.0}


def test_shadow_benchmark_only_pays_out_what_it_holds():
    history = [(day(0), 100.0), (day(10), 50.0), (day(20), 50.0)]
    values, moved = _benchmark_shadow_portfolio(
        history, day(0), day(20), 100.0, {day(10): -80.0}, set()
    )
    assert moved == {day(10): -50.0}
    assert values[-1] == (day(20), 0.0)


async def _dip_and_top_up(session: AsyncSession, user: User, workspace) -> tuple[date, date, date]:
    """A fund that halves and recovers, plus a second one bought at the dip."""
    end = date.today()
    start = end - timedelta(days=30)
    mid = start + timedelta(days=15)
    first, second = (
        Asset(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace.id,
            name=name,
            type="fund",
            currency="BRL",
            valuation_method="manual",
            purchase_date=bought,
            purchase_price=Decimal("100"),
        )
        for name, bought in (("First", start), ("Second", mid))
    )
    session.add_all([first, second])
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=workspace.id,
            amount=Decimal(amount),
            date=when,
            source="manual",
        )
        for asset, when, amount in (
            (first, start, "100"),
            (first, mid, "50"),
            (first, end, "100"),
            (second, mid, "100"),
            (second, end, "200"),
        )
    )
    await session.commit()
    return start, mid, end


@pytest.mark.asyncio
async def test_money_weighted_performance_compares_shadow_benchmarks(
    session: AsyncSession, test_user: User, test_workspace
):
    start, mid, end = await _dip_and_top_up(session, test_user, test_workspace)
    provider = MultiHistoryProvider(
        {
            # Moves exactly like the portfolio: same money, same result.
            "^SAME": [
                BenchmarkHistoryPoint(start, 100.0),
                BenchmarkHistoryPoint(mid, 50.0),
                BenchmarkHistoryPoint(end, 100.0),
            ],
            "^FLAT": [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)],
        }
    )
    expected = (36 / (1 + math.sqrt(13)) ** 2 - 1) * 100

    twr = await get_portfolio_performance_multi(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        [("yahoo", "^SAME"), ("yahoo", "^FLAT")],
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    assert twr.method == "twr"
    assert twr.portfolio_return == pytest.approx(0.0)
    assert twr.money_weighted is None

    mwr = await get_portfolio_performance_multi(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        [("yahoo", "^SAME"), ("yahoo", "^FLAT")],
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
        method="mwr",
    )
    assert mwr.method == "mwr"
    assert mwr.portfolio_return == pytest.approx(expected, abs=1e-5)
    same, flat = mwr.benchmarks
    assert same.benchmark_return == pytest.approx(expected, abs=1e-5)
    assert same.excess_return == pytest.approx(0.0, abs=1e-5)
    assert flat.benchmark_return == pytest.approx(0.0, abs=1e-5)

    series = mwr.money_weighted
    assert series is not None
    dates = [point.date for point in mwr.points]
    assert len(series.values) == len(series.flows) == len(dates)
    assert series.values[0] == 100.0 and series.values[-1] == 300.0
    assert series.flows[dates.index(mid)] == 100.0
    assert sum(series.flows) == 100.0
    assert series.benchmark_values["yahoo:^FLAT"][-1] == 200.0
    assert series.benchmark_flows["yahoo:^SAME"] == series.flows


@pytest.mark.asyncio
async def test_performance_endpoint_switches_method(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
):
    await _dip_and_top_up(session, test_user, test_workspace)
    endpoint = "/api/assets/performance"

    default = (await client.get(endpoint, params={"period": "3m"}, headers=auth_headers)).json()
    assert default["method"] == "twr"
    assert default["money_weighted"] is None

    response = await client.get(
        endpoint, params={"period": "3m", "method": "mwr"}, headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "mwr"
    assert data["portfolio_return"] > 60
    assert len(data["money_weighted"]["values"]) == len(data["points"])

    invalid = await client.get(endpoint, params={"method": "irr"}, headers=auth_headers)
    assert invalid.status_code == 422
