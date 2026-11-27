"""Benchmark symbols that differ only in case are one comparison."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.benchmark import BenchmarkHistoryPoint


class _Provider:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    async def history(self, provider_name, symbol, start_date, end_date):
        self.symbols.append(symbol)
        return [BenchmarkHistoryPoint(start_date, 100.0), BenchmarkHistoryPoint(end_date, 110.0)]


async def _seed(session: AsyncSession, user: User, workspace) -> None:
    end = date.today()
    start = end - timedelta(days=30)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(asset)
    await session.flush()
    for day, amount in ((start, "100"), (end, "110")):
        session.add(
            AssetValue(
                asset_id=asset.id,
                workspace_id=workspace.id,
                amount=Decimal(amount),
                date=day,
                source="manual",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_symbols_differing_only_in_case_are_one_benchmark(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    await _seed(session, test_user, test_workspace)
    fake = _Provider()
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: fake
    )
    response = await client.get(
        "/api/assets/performance",
        params=[
            ("provider", "yahoo"),
            ("benchmark", " spy "),
            ("provider", "yahoo"),
            ("benchmark", "SPY"),
            ("period", "3m"),
        ],
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert len(fake.symbols) == 1
    # The key is the spelling the client sent first, so it finds its series.
    # Lowercase first: an API that upper-cased every key would fail here.
    assert [item["key"] for item in response.json()["benchmarks"]] == ["yahoo:spy"]


@pytest.mark.asyncio
async def test_case_duplicates_do_not_count_toward_the_limit(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: _Provider()
    )
    params: list[tuple[str, str]] = []
    for symbol in ("^A", "^B", "^C", "^D", "^E", "^e"):
        params.extend([("provider", "yahoo"), ("benchmark", symbol)])
    response = await client.get(
        "/api/assets/performance", params=[*params, ("period", "3m")], headers=auth_headers
    )
    assert response.status_code == 200

    params.extend([("provider", "yahoo"), ("benchmark", "^F")])
    response = await client.get(
        "/api/assets/performance", params=[*params, ("period", "3m")], headers=auth_headers
    )
    assert response.status_code == 422
