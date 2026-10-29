"""MCP access to the investment ledger (issue #1211).

Covers the two halves: `list_assets` passing through the cost-basis
fields the service already computes, and the new
`list_asset_transactions` tool over `asset_transaction_service`.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.user import User
from app.schemas.asset import AssetTransactionCreate
from app.services import asset_transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def wallet(session: AsyncSession, test_user: User, test_workspace) -> AssetGroup:
    group = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="US Stocks",
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


@pytest_asyncio.fixture
async def holding(
    session: AsyncSession, test_user: User, test_workspace, wallet: AssetGroup
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Microsoft", type="stock", currency="USD",
        valuation_method="market_price", ticker="MSFT",
        last_price=Decimal("40.00"), group_id=wallet.id,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def _buy(session, workspace, asset, qty, price, when, kind="buy"):
    return await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(
            kind=kind, quantity=Decimal(qty), price=Decimal(price), date=when
        ),
    )


# --- Registry --------------------------------------------------------------

async def test_registry_exposes_the_ledger_tool():
    assert "list_asset_transactions" in REGISTRY
    spec = REGISTRY["list_asset_transactions"]
    assert not spec.is_proposal
    assert spec.parameters["type"] == "object"
    assert set(spec.parameters["properties"]) == {
        "asset_id", "ticker", "kind", "from_date", "to_date", "limit",
    }


# --- list_assets passthrough ----------------------------------------------

async def test_list_assets_carries_cost_basis_and_wallet_name(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await _buy(session, test_workspace, holding, "5", "26", date(2026, 2, 1))

    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    item = next(i for i in r["items"] if i["ticker"] == "MSFT")

    # avg = (200 + 130) / 15 = 22.00, cost basis 330
    assert round(item["average_price"], 2) == 22.00
    assert round(item["total_invested"], 2) == 330.00
    assert item["transaction_count"] == 2
    assert item["realized_gain"] in (0, 0.0, None)
    assert item["group_name"] == "US Stocks"
    # gain_loss stays the unrealized P&L: 15 units @ 40 = 600, less 330
    assert round(item["gain_loss"], 2) == 270.00


async def test_list_assets_without_a_ledger_reports_zero_transactions(
    session: AsyncSession, ctx: CallContext, holding: Asset
):
    """A holding with no ledger must not pretend to have a cost basis."""
    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    item = next(i for i in r["items"] if i["ticker"] == "MSFT")
    assert item["transaction_count"] == 0
    assert item["average_price"] is None


# --- list_asset_transactions ----------------------------------------------

async def test_ledger_empty_returns_stable_shape(session: AsyncSession, ctx: CallContext):
    r = await REGISTRY["list_asset_transactions"].handler(session=session, ctx=ctx)
    assert r == {"items": [], "total": 0}


async def test_ledger_returns_newest_first_with_asset_context(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await _buy(session, test_workspace, holding, "5", "26", date(2026, 2, 1))

    r = await REGISTRY["list_asset_transactions"].handler(session=session, ctx=ctx)
    assert r["total"] == 2
    assert [t["date"] for t in r["items"]] == ["2026-02-01", "2026-01-01"]
    first = r["items"][0]
    assert first["asset_name"] == "Microsoft"
    assert first["ticker"] == "MSFT"
    assert first["currency"] == "USD"
    assert first["kind"] == "buy"
    assert first["quantity"] == 5
    assert first["price"] == 26


async def test_ledger_filters_by_kind_and_ticker(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await _buy(session, test_workspace, holding, "4", "30", date(2026, 3, 1), kind="sell")
    handler = REGISTRY["list_asset_transactions"].handler

    sells = await handler(session=session, ctx=ctx, kind="sell")
    assert [t["kind"] for t in sells["items"]] == ["sell"]

    # ticker match is case-insensitive
    assert (await handler(session=session, ctx=ctx, ticker="msft"))["total"] == 2
    assert (await handler(session=session, ctx=ctx, ticker="PETR4.SA"))["total"] == 0


async def test_ledger_kind_casing_does_not_widen_the_filter(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """The service drops a kind it does not recognise, which would return
    everything while looking filtered. Casing must not trigger that."""
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await _buy(session, test_workspace, holding, "4", "30", date(2026, 3, 1), kind="sell")
    handler = REGISTRY["list_asset_transactions"].handler

    for spelling in ("sell", "Sell", "SELL"):
        r = await handler(session=session, ctx=ctx, kind=spelling)
        assert [t["kind"] for t in r["items"]] == ["sell"], spelling


async def test_ledger_filters_by_asset_id(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    handler = REGISTRY["list_asset_transactions"].handler

    assert (await handler(session=session, ctx=ctx, asset_id=str(holding.id)))["total"] == 1
    other = await handler(session=session, ctx=ctx, asset_id=str(uuid.uuid4()))
    assert other["total"] == 0


async def test_ledger_date_range_is_applied_before_the_limit(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """The point of filtering in SQL: a narrow range must return the rows
    inside it, not whatever survives truncating the newest page first."""
    for day in range(1, 6):
        await _buy(session, test_workspace, holding, "1", "10", date(2026, 5, day))
    handler = REGISTRY["list_asset_transactions"].handler

    windowed = await handler(
        session=session, ctx=ctx,
        from_date="2026-05-02", to_date="2026-05-03", limit=1,
    )
    # Newest inside the window, not the newest overall (2026-05-05).
    assert [t["date"] for t in windowed["items"]] == ["2026-05-03"]

    both = await handler(session=session, ctx=ctx, from_date="2026-05-02", to_date="2026-05-03")
    assert [t["date"] for t in both["items"]] == ["2026-05-03", "2026-05-02"]


async def test_ledger_limit_is_capped(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    handler = REGISTRY["list_asset_transactions"].handler
    # Over the cap and under the floor both resolve to a usable query.
    assert (await handler(session=session, ctx=ctx, limit=10_000))["total"] == 1
    assert (await handler(session=session, ctx=ctx, limit=0))["total"] == 1
