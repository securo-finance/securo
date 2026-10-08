"""A wallet belongs to a workspace, and so does everything it totals.

`Asset.group_id` is a plain foreign key. Nothing checked that the id a
request supplied named a wallet in the request's own workspace, and the
wallet rollup selected assets on `group_id` alone. An asset created,
bought or imported against another workspace's wallet id was therefore
counted in that wallet's item count and value, so one workspace's
holdings surfaced inside another workspace's totals.

The rule these tests pin: a `group_id` accepted from a request must name
a wallet in the request's own workspace, and the rollup counts only
assets belonging to the wallet's workspace whatever the column says.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.market_price import (
    MarketPriceProvider,
    MarketSymbolMatch,
    MarketSymbolQuote,
)
from app.schemas.asset import AssetBuyCreate, AssetCreate, AssetUpdate
from app.services import asset_import_service, asset_service, asset_transaction_service
from app.services.asset_group_service import get_group


class FakeProvider(MarketPriceProvider):
    """Quotes every ticker, so a rejection is the guard's doing rather than a
    lookup that failed on the way to the write."""

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_quote(self, symbol: str) -> MarketSymbolQuote:
        return MarketSymbolQuote(
            symbol=symbol.upper(), name=f"{symbol.upper()} Inc", price=100.0,
            currency="BRL", exchange="XNAS", quote_type="EQUITY", logo_url=None,
        )


async def _second_workspace(session: AsyncSession, user_id: uuid.UUID) -> Workspace:
    """A second workspace the same user belongs to — a shared household one,
    or one they were added to. Its wallets are off-limits from the first."""
    ws = Workspace(
        id=uuid.uuid4(), name="Shared", kind="personal",
        created_by_user_id=user_id, default_currency="BRL",
    )
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=user_id, role="owner")
    )
    await session.commit()
    await session.refresh(ws)
    return ws


async def _wallet(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID, name: str
) -> AssetGroup:
    group = AssetGroup(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name,
    )
    session.add(group)
    await session.commit()
    return group


# ---------------------------------------------------------------------------
# the write paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_a_wallet_from_another_workspace(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    other = await _second_workspace(session, test_user.id)
    theirs = await _wallet(session, test_user.id, other.id, "Their Wallet")

    with pytest.raises(HTTPException) as exc:
        await asset_service.create_asset(
            session, test_workspace.id, test_user.id,
            AssetCreate(name="Apartment", type="real_estate", currency="BRL",
                        group_id=theirs.id),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_rejects_a_move_into_another_workspace(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    other = await _second_workspace(session, test_user.id)
    theirs = await _wallet(session, test_user.id, other.id, "Their Wallet")
    asset = await asset_service.create_asset(
        session, test_workspace.id, test_user.id,
        AssetCreate(name="Apartment", type="real_estate", currency="BRL"),
    )

    with pytest.raises(HTTPException) as exc:
        await asset_service.update_asset(
            session, asset.id, test_workspace.id, test_user.id,
            AssetUpdate(group_id=theirs.id),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_still_clears_the_wallet(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """Ungrouping is an explicit null, which the guard must let through."""
    mine = await _wallet(session, test_user.id, test_workspace.id, "My Wallet")
    asset = await asset_service.create_asset(
        session, test_workspace.id, test_user.id,
        AssetCreate(name="Apartment", type="real_estate", currency="BRL", group_id=mine.id),
    )

    updated = await asset_service.update_asset(
        session, asset.id, test_workspace.id, test_user.id, AssetUpdate(group_id=None),
    )

    assert updated is not None
    assert updated.group_id is None


@pytest.mark.asyncio
async def test_buy_rejects_a_wallet_from_another_workspace(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    other = await _second_workspace(session, test_user.id)
    theirs = await _wallet(session, test_user.id, other.id, "Their Wallet")

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.buy_into_holding(
            session, test_workspace.id, test_user.id,
            AssetBuyCreate(ticker="AAPL", quantity=Decimal("10"), price=Decimal("100"),
                           date=date(2026, 1, 15), group_id=theirs.id),
            market_provider=FakeProvider(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_rejects_a_wallet_from_another_workspace(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    other = await _second_workspace(session, test_user.id)
    theirs = await _wallet(session, test_user.id, other.id, "Their Wallet")
    orders, _, _ = asset_import_service.parse_orders_csv(
        b"ticker,date,quantity,price\nAAPL,2026-01-15,10,100.00"
    )

    with pytest.raises(HTTPException) as exc:
        await asset_import_service.import_orders(
            session, test_workspace.id, test_user.id, orders,
            group_id=theirs.id, market_provider=FakeProvider(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_preview_rejects_it_too(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The preview runs the same function with `dry_run`, and is what the UI
    calls first — it has to refuse there rather than promise an import that
    the commit will reject."""
    other = await _second_workspace(session, test_user.id)
    theirs = await _wallet(session, test_user.id, other.id, "Their Wallet")
    orders, _, _ = asset_import_service.parse_orders_csv(
        b"ticker,date,quantity,price\nAAPL,2026-01-15,10,100.00"
    )

    with pytest.raises(HTTPException) as exc:
        await asset_import_service.import_orders(
            session, test_workspace.id, test_user.id, orders,
            group_id=theirs.id, dry_run=True, market_provider=FakeProvider(),
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# the read path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_ignores_an_asset_from_another_workspace(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The backstop, for rows already written before the guard existed."""
    other = await _second_workspace(session, test_user.id)
    mine = await _wallet(session, test_user.id, test_workspace.id, "My Wallet")

    session.add_all([
        Asset(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
              name="Mine", type="investment", currency="BRL",
              purchase_price=Decimal("100"), group_id=mine.id),
        Asset(id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id,
              name="Theirs", type="investment", currency="BRL",
              purchase_price=Decimal("900"), group_id=mine.id),
    ])
    await session.commit()

    read = await get_group(session, mine.id, test_workspace.id, test_user.id)

    assert read is not None
    assert read.asset_count == 1
    assert read.current_value == 100.0
    assert read.current_value_primary == 100.0
