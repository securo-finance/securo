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
from app.core.app_clock import app_today
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.fx_rate import FxRate
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.asset import AssetTransactionCreate, AssetUpdate
from app.services import asset_service, asset_transaction_service
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


async def test_list_assets_converts_to_the_primary_currency(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """A USD holding for a BRL user comes back in BRL too, so an agent can
    total a mixed-currency portfolio without converting anything itself."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    await _buy(session, test_workspace, holding, "15", "22", date(2026, 1, 1))

    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    assert r["primary_currency"] == "BRL"
    item = next(i for i in r["items"] if i["ticker"] == "MSFT")
    # 15 units @ 40 USD = 600 USD -> 3000 BRL; gain 600 - 330 = 270 -> 1350
    assert item["current_value_primary"] == pytest.approx(3000.0)
    assert item["gain_loss_primary"] == pytest.approx(1350.0)


async def test_list_assets_leaves_primary_null_without_a_rate(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """No FX rate must read as 'not converted', never as a 1:1 amount."""
    await _buy(session, test_workspace, holding, "15", "22", date(2026, 1, 1))

    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    item = next(i for i in r["items"] if i["ticker"] == "MSFT")
    assert item["current_value"] == pytest.approx(600.0)
    assert item["current_value_primary"] is None
    assert item["gain_loss_primary"] is None


async def test_list_assets_fills_primary_for_same_currency_holdings(
    session: AsyncSession, ctx: CallContext, test_user: User, test_workspace
):
    """Unlike the REST list, null here only ever means a missing rate."""
    local = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Petrobras", type="stock", currency="BRL",
        valuation_method="market_price", ticker="PETR4.SA",
        last_price=Decimal("30.00"),
    )
    session.add(local)
    await session.commit()
    await _buy(session, test_workspace, local, "10", "25", date(2026, 1, 1))

    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    item = next(i for i in r["items"] if i["ticker"] == "PETR4.SA")
    assert item["current_value_primary"] == pytest.approx(item["current_value"])
    assert item["gain_loss_primary"] == pytest.approx(50.0)


async def test_list_assets_fetches_a_missing_rate_once_per_request(
    session: AsyncSession, ctx: CallContext, test_user: User, test_workspace,
    holding: Asset, monkeypatch,
):
    """Without a per-request cache every USD holding retried the provider,
    twice each (value and gain), and each retry can wait on its timeout."""
    from app.services import fx_rate_service

    calls: list = []

    async def no_rates(*args, **kwargs):
        calls.append(args)
        return 0

    monkeypatch.setattr(fx_rate_service, "sync_rates", no_rates)
    await _buy(session, test_workspace, holding, "15", "22", date(2026, 1, 1))
    for ticker in ("AAPL", "GOOG"):
        extra = Asset(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            name=ticker, type="stock", currency="USD",
            valuation_method="market_price", ticker=ticker,
            last_price=Decimal("10.00"),
        )
        session.add(extra)
        await session.commit()
        await _buy(session, test_workspace, extra, "1", "10", date(2026, 1, 1))

    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    assert len(calls) == 1
    assert all(i["current_value_primary"] is None for i in r["items"])


async def test_list_assets_resolves_a_stored_rate_once_per_request(
    session: AsyncSession, ctx: CallContext, test_user: User, test_workspace,
    holding: Asset, monkeypatch,
):
    """The missing-rate test above would still pass if only misses were
    cached, so this one counts lookups when the rate exists."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    await _buy(session, test_workspace, holding, "15", "22", date(2026, 1, 1))
    for ticker in ("AAPL", "GOOG"):
        extra = Asset(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            name=ticker, type="stock", currency="USD",
            valuation_method="market_price", ticker=ticker,
            last_price=Decimal("10.00"),
        )
        session.add(extra)
        await session.commit()
        await _buy(session, test_workspace, extra, "1", "10", date(2026, 1, 1))

    real = asset_service.convert_or_none
    lookups: list = []

    async def counting(*args, **kwargs):
        lookups.append(args[2:4])
        return await real(*args, **kwargs)

    monkeypatch.setattr(asset_service, "convert_or_none", counting)
    r = await REGISTRY["list_assets"].handler(session=session, ctx=ctx)
    assert lookups == [("USD", "BRL")]
    values = {i["ticker"]: i["current_value_primary"] for i in r["items"]}
    # 15 x 40 USD and 1 x 10 USD, all at 5 BRL per USD.
    assert values == pytest.approx({"MSFT": 3000.0, "AAPL": 50.0, "GOOG": 50.0})


# --- list_asset_transactions ----------------------------------------------

async def test_ledger_empty_returns_stable_shape(session: AsyncSession, ctx: CallContext):
    r = await REGISTRY["list_asset_transactions"].handler(session=session, ctx=ctx)
    assert r == {"items": [], "total": 0, "has_more": False}


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


async def test_ledger_rejects_a_kind_outside_buy_and_sell(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """An unknown kind used to fall through the service filter and return
    the whole ledger as if filtered; the REST API answers 422 instead."""
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    handler = REGISTRY["list_asset_transactions"].handler

    for kind in ("dividend", "Staking", "transfer", ""):
        with pytest.raises(ValueError, match="'buy' or 'sell'"):
            await handler(session=session, ctx=ctx, kind=kind)


async def test_ledger_filters_by_asset_id(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    handler = REGISTRY["list_asset_transactions"].handler

    assert (await handler(session=session, ctx=ctx, asset_id=str(holding.id)))["total"] == 1
    other = await handler(session=session, ctx=ctx, asset_id=str(uuid.uuid4()))
    assert other["total"] == 0


async def test_ledger_is_scoped_to_the_callers_workspace(
    session: AsyncSession, ctx: CallContext, test_user: User, test_workspace,
    holding: Asset,
):
    """Every other ledger test seeds one workspace, so they would still pass
    if the workspace filter in the query were dropped."""
    other_ws = Workspace(
        id=uuid.uuid4(), name="Second", kind="personal",
        created_by_user_id=test_user.id, default_currency="BRL", locale="pt-BR",
    )
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=other_ws.id, user_id=test_user.id, role="owner",
    ))
    foreign = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_ws.id,
        name="Microsoft", type="stock", currency="USD",
        valuation_method="market_price", ticker="MSFT",
        last_price=Decimal("40.00"),
    )
    session.add(foreign)
    await session.commit()
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await _buy(session, other_ws, foreign, "3", "30", date(2026, 1, 2))
    handler = REGISTRY["list_asset_transactions"].handler

    # Same ticker in both workspaces, so only the workspace filter separates them.
    mine = await handler(session=session, ctx=ctx, ticker="MSFT")
    assert [i["asset_id"] for i in mine["items"]] == [str(holding.id)]
    theirs = await handler(
        session=session,
        ctx=CallContext(user_id=test_user.id, workspace_id=other_ws.id),
        ticker="MSFT",
    )
    assert [i["asset_id"] for i in theirs["items"]] == [str(foreign.id)]


async def test_ledger_ticker_filter_survives_a_lowercase_rename(
    session: AsyncSession, ctx: CallContext, test_user: User, test_workspace,
    holding: Asset,
):
    """The filter uppercases its input, so a ticker stored lowercase by an
    edit would never match its own transactions."""
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    await asset_service.update_asset(
        session, holding.id, test_workspace.id, test_user.id,
        AssetUpdate(ticker="msf.de"),
    )
    handler = REGISTRY["list_asset_transactions"].handler

    for ticker in ("MSF.DE", "msf.de"):
        assert (await handler(session=session, ctx=ctx, ticker=ticker))["total"] == 1


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


async def test_ledger_limit_is_clamped_before_the_query(
    session: AsyncSession, ctx: CallContext, monkeypatch
):
    """Checks the limit the service receives, which a one-row fixture
    could not tell apart from an uncapped query. The tool asks for one row
    past the cap so it can tell a full page from a truncated one."""
    seen: list[int] = []

    async def fake_list(*args, **kwargs):
        seen.append(kwargs["limit"])
        return []

    monkeypatch.setattr(
        asset_transaction_service, "list_workspace_transactions", fake_list
    )
    handler = REGISTRY["list_asset_transactions"].handler
    for limit in (10_000, 0, None):
        await handler(session=session, ctx=ctx, limit=limit)
    assert seen == [501, 2, 101]


async def test_ledger_null_limit_falls_back_to_the_default(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    await _buy(session, test_workspace, holding, "10", "20", date(2026, 1, 1))
    r = await REGISTRY["list_asset_transactions"].handler(
        session=session, ctx=ctx, limit=None
    )
    assert r["total"] == 1


async def test_ledger_flags_a_truncated_page(
    session: AsyncSession, ctx: CallContext, test_workspace, holding: Asset
):
    """`total` counts the rows returned, so without `has_more` a page cut
    at the limit would look like the complete ledger."""
    for day in range(1, 4):
        await _buy(session, test_workspace, holding, "1", "10", date(2026, 5, day))
    handler = REGISTRY["list_asset_transactions"].handler

    cut = await handler(session=session, ctx=ctx, limit=2)
    assert (cut["total"], cut["has_more"]) == (2, True)
    whole = await handler(session=session, ctx=ctx, limit=3)
    assert (whole["total"], whole["has_more"]) == (3, False)
