from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.benchmark import (
    BenchmarkHistoryPoint,
    BenchmarkProviderError,
    BenchmarkRateLimitedError,
)
from app.schemas.asset import BenchmarkMatch


class FakeBenchmarkProvider:
    def __init__(self, *, history_error: Exception | None = None, empty: bool = False) -> None:
        self.history_error = history_error
        self.empty = empty
        self.search_calls = 0
        self.history_calls = 0

    async def search(self, query: str, limit: int = 15):
        self.search_calls += 1
        return [BenchmarkMatch(symbol="^TEST", name="Test Index", exchange="SNP", provider="yahoo")]

    async def history(self, provider_name, symbol, start_date, end_date):
        self.history_calls += 1
        if self.history_error:
            raise self.history_error
        if self.empty:
            return []
        return [
            BenchmarkHistoryPoint(start_date, 100.0),
            BenchmarkHistoryPoint(end_date, 110.0),
        ]


async def _seed_performance_asset(session: AsyncSession, user: User, workspace) -> uuid.UUID:
    end = date.today()
    start = end - timedelta(days=30)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="API Fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=asset.id,
                workspace_id=workspace.id,
                amount=Decimal("110"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()
    return asset.id


@pytest.mark.asyncio
async def test_benchmark_search_is_authenticated_and_dynamic(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr("app.api.assets.get_benchmark_provider", lambda: fake)
    assert (
        await client.get("/api/assets/benchmarks/search", params={"q": "test"})
    ).status_code == 401
    response = await client.get(
        "/api/assets/benchmarks/search", params={"q": "test"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == [
        {"symbol": "^TEST", "name": "Test Index", "exchange": "SNP", "provider": "yahoo"}
    ]
    assert fake.search_calls == 1


@pytest.mark.asyncio
async def test_performance_endpoint_returns_aligned_metrics(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    await _seed_performance_asset(session, test_user, test_workspace)
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: fake
    )
    response = await client.get(
        "/api/assets/performance",
        params={"provider": "yahoo", "benchmark": "^TEST", "period": "3m"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return"] == pytest.approx(10.0)
    assert data["benchmark_return"] == pytest.approx(10.0)
    assert data["excess_return"] == pytest.approx(0.0)
    assert data["benchmarks"] == [
        {
            "key": "yahoo:^TEST",
            "symbol": "^TEST",
            "provider": "yahoo",
            "benchmark_return": pytest.approx(10.0),
            "excess_return": pytest.approx(0.0),
            "source_error": None,
        }
    ]
    assert data["points"][-1]["benchmarks"]["yahoo:^TEST"] == pytest.approx(10.0)
    assert len(data["points"]) >= 2


@pytest.mark.asyncio
async def test_performance_endpoint_renders_portfolio_without_benchmark(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    await _seed_performance_asset(session, test_user, test_workspace)

    def fail_if_called():
        raise AssertionError("portfolio-only performance must not load a benchmark provider")

    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider",
        fail_if_called,
    )
    response = await client.get(
        "/api/assets/performance",
        params={"period": "3m"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return"] == pytest.approx(10.0)
    assert data["benchmark_symbol"] is None
    assert data["benchmark_provider"] is None
    assert data["benchmark_return"] is None
    assert data["excess_return"] is None
    assert data["benchmarks"] == []
    assert data["points"][-1]["benchmarks"] == {}


@pytest.mark.asyncio
async def test_performance_endpoint_accepts_multiple_benchmarks(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    await _seed_performance_asset(session, test_user, test_workspace)
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: fake
    )
    response = await client.get(
        "/api/assets/performance",
        params=[
            ("provider", "yahoo"),
            ("provider", "b3"),
            ("benchmark", "^TEST"),
            ("benchmark", "B3:DI"),
            ("period", "3m"),
        ],
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert fake.history_calls == 2
    assert [item["key"] for item in data["benchmarks"]] == [
        "yahoo:^TEST",
        "b3:B3:DI",
    ]
    assert set(data["points"][-1]["benchmarks"]) == {
        "yahoo:^TEST",
        "b3:B3:DI",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_error"),
    [
        (FakeBenchmarkProvider(empty=True), "no_data"),
        (FakeBenchmarkProvider(history_error=BenchmarkRateLimitedError("slow")), "rate_limited"),
        (FakeBenchmarkProvider(history_error=BenchmarkProviderError("down")), "unavailable"),
    ],
)
async def test_performance_preserves_portfolio_and_reports_source_failures(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
    provider,
    expected_error,
):
    await _seed_performance_asset(session, test_user, test_workspace)
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider",
        lambda: provider,
    )
    response = await client.get(
        "/api/assets/performance",
        params={"provider": "yahoo", "benchmark": "^TEST"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return"] == pytest.approx(10.0)
    assert data["benchmarks"][0]["source_error"] == expected_error
    assert data["benchmark_return"] is None
    assert data["excess_return"] is None
    assert len(data["points"]) >= 2
    assert all(point["benchmarks"] == {} for point in data["points"])
    without_benchmark = await client.get("/api/assets/performance", headers=auth_headers)
    assert data["points"] == without_benchmark.json()["points"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_symbol", ["^FIRST", "^SECOND"])
async def test_performance_keeps_healthy_benchmarks_when_another_source_fails(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
    failed_symbol,
):
    await _seed_performance_asset(session, test_user, test_workspace)

    class MixedProvider(FakeBenchmarkProvider):
        async def history(self, provider_name, symbol, start_date, end_date):
            if symbol == failed_symbol:
                raise BenchmarkProviderError("source failed")
            return await super().history(provider_name, symbol, start_date, end_date)

    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: MixedProvider()
    )
    response = await client.get(
        "/api/assets/performance",
        params=[
            ("provider", "yahoo"),
            ("provider", "yahoo"),
            ("benchmark", "^FIRST"),
            ("benchmark", "^SECOND"),
        ],
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return"] == pytest.approx(10.0)
    failed = next(item for item in data["benchmarks"] if item["symbol"] == failed_symbol)
    healthy = next(item for item in data["benchmarks"] if item["symbol"] != failed_symbol)
    assert failed["source_error"] == "unavailable"
    assert failed["benchmark_return"] is None
    assert failed["excess_return"] is None
    assert healthy["source_error"] is None
    assert healthy["benchmark_return"] is not None
    assert healthy["excess_return"] is not None
    assert set(data["points"][-1]["benchmarks"]) == {healthy["key"]}
    if failed_symbol == "^FIRST":
        assert data["benchmark_return"] is None
        assert all(point["benchmark"] == 0 for point in data["points"])


@pytest.mark.asyncio
async def test_regular_asset_list_does_not_touch_benchmark_provider(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    def fail_if_called():
        raise AssertionError("benchmark provider must stay lazy")

    monkeypatch.setattr("app.api.assets.get_benchmark_provider", fail_if_called)
    response = await client.get("/api/assets", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_performance_authentication_and_parameter_validation(
    client: AsyncClient, auth_headers: dict
):
    endpoint = "/api/assets/performance"
    valid = {"provider": "yahoo", "benchmark": "^TEST"}
    assert (await client.get(endpoint, params=valid)).status_code == 401
    assert (
        await client.get(
            endpoint,
            params={**valid, "provider": "unknown"},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.get(
            endpoint,
            params=[
                ("provider", "yahoo"),
                ("provider", "b3"),
                ("benchmark", "^TEST"),
            ],
            headers=auth_headers,
        )
    ).status_code == 422
    too_many: list[tuple[str, str | int | float | None]] = []
    for index in range(6):
        too_many.extend([("provider", "yahoo"), ("benchmark", f"^TEST{index}")])
    assert (await client.get(endpoint, params=too_many, headers=auth_headers)).status_code == 422
    assert (
        await client.get(
            endpoint,
            params={**valid, "period": "10y"},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.get(
            endpoint,
            params={**valid, "asset_group_ids": "not-a-uuid"},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.get(
            "/api/assets/benchmarks/search",
            params={"q": "x"},
            headers=auth_headers,
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_insufficient_portfolio_does_not_fetch_benchmark(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider",
        lambda: fake,
    )
    response = await client.get(
        "/api/assets/performance",
        params={"provider": "yahoo", "benchmark": "^TEST"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["points"] == []
    assert response.json()["portfolio_return"] is None
    assert fake.history_calls == 0


@pytest.mark.asyncio
async def test_performance_endpoint_filters_to_individual_assets(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    selected_id = await _seed_performance_asset(session, test_user, test_workspace)
    end = date.today()
    start = end - timedelta(days=30)
    excluded = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Excluded API fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(excluded)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=excluded.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=excluded.id,
                workspace_id=test_workspace.id,
                amount=Decimal("200"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: fake
    )

    response = await client.get(
        "/api/assets/performance",
        params={
            "provider": "yahoo",
            "benchmark": "^TEST",
            "period": "3m",
            "asset_ids": str(selected_id),
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["portfolio_return"] == pytest.approx(10.0)


def _benchmarks_disabled(monkeypatch) -> FakeBenchmarkProvider:
    fake = FakeBenchmarkProvider()
    monkeypatch.setattr("app.api.assets.get_benchmark_provider", lambda: fake)
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.get_benchmark_provider", lambda: fake
    )
    monkeypatch.setattr(
        "app.api.assets.get_settings",
        lambda: SimpleNamespace(performance_benchmarks_enabled=False),
    )
    return fake


@pytest.mark.asyncio
async def test_disabled_benchmarks_never_reach_an_external_source(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    await _seed_performance_asset(session, test_user, test_workspace)
    fake = _benchmarks_disabled(monkeypatch)

    search = await client.get(
        "/api/assets/benchmarks/search", params={"q": "test"}, headers=auth_headers
    )
    assert search.status_code == 404

    response = await client.get(
        "/api/assets/performance",
        params={"provider": "yahoo", "benchmark": "^TEST", "period": "3m"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return"] == pytest.approx(10.0)
    assert data["benchmarks"] == []
    assert fake.search_calls == fake.history_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "expected"), [(None, True), ("false", False)])
async def test_info_reports_whether_benchmarks_are_enabled(
    client: AsyncClient, monkeypatch, env, expected
):
    if env is None:
        monkeypatch.delenv("PERFORMANCE_BENCHMARKS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PERFORMANCE_BENCHMARKS_ENABLED", env)
    get_settings.cache_clear()  # read the environment again
    try:
        response = await client.get("/api/info")
    finally:
        get_settings.cache_clear()
    assert response.json()["features"]["performance_benchmarks"] is expected


@pytest.mark.asyncio
async def test_info_and_routes_agree_when_only_settings_turn_benchmarks_off(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Turned off in backend/.env, which never reaches os.environ."""
    monkeypatch.setenv("PERFORMANCE_BENCHMARKS_ENABLED", "true")
    monkeypatch.setattr(get_settings(), "performance_benchmarks_enabled", False)

    info = await client.get("/api/info")
    search = await client.get(
        "/api/assets/benchmarks/search", params={"q": "test"}, headers=auth_headers
    )

    assert info.json()["features"]["performance_benchmarks"] is False
    assert search.status_code == 404
