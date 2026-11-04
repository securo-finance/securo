"""A synced brokerage arrives twice: as an Account carrying the portfolio
total and as the holdings that make up that total (#343). Net worth must
count one of them, and must keep counting the account before the holdings
have any value history of their own."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.services.connection_service import (
    _wallet_external_id,
    accounts_superseded_by_assets,
)
from app.services.dashboard_service import get_summary
from app.services.report_service import _net_worth_at


async def _seed_brokerage(
    session: AsyncSession,
    user,
    workspace,
    *,
    balance: str = "1000.00",
    holding: str = "1000.00",
    value_date: date | None = None,
    account_type: str = "investment",
    archived: bool = False,
) -> tuple[Account, Asset]:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        provider="simplefin", external_id=f"conn-{uuid.uuid4().hex[:8]}",
        institution_name="Broker", credentials={"token": "x"}, status="active",
        last_sync_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        connection_id=conn.id, external_id=f"acct-{uuid.uuid4().hex[:8]}",
        name="Brokerage", type=account_type, balance=Decimal(balance), currency="USD",
    )
    session.add(account)
    group = AssetGroup(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name="Brokerage holdings", source="sync", connection_id=conn.id,
        external_id=_wallet_external_id(conn.external_id, account.external_id),
    )
    session.add(group)
    await session.flush()
    asset = Asset(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name="ETF", type="investment", currency="USD", group_id=group.id,
        connection_id=conn.id, source="sync", is_archived=archived,
    )
    session.add(asset)
    await session.flush()
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=workspace.id,
        amount=Decimal(holding), date=value_date or date.today(), source="sync",
    ))
    await session.commit()
    return account, asset


@pytest.mark.asyncio
async def test_synced_brokerage_is_superseded_by_its_holdings(
    session: AsyncSession, test_user, test_workspace
):
    account, _ = await _seed_brokerage(session, test_user, test_workspace)
    assert await accounts_superseded_by_assets(session, test_workspace.id) == {account.id}


@pytest.mark.asyncio
async def test_net_worth_counts_a_synced_brokerage_once(
    session: AsyncSession, test_user, test_workspace
):
    await _seed_brokerage(session, test_user, test_workspace, balance="1000.00", holding="990.00")
    dp = await _net_worth_at(session, test_workspace.id, date.today(), "USD")
    # The holdings carry the value; the account's copy of it is dropped.
    assert dp.breakdowns["accounts"] == 0
    assert dp.breakdowns["assets"] == 990.0
    assert dp.value == 990.0
    assert [c.group for c in dp.composition] == ["assets"]


@pytest.mark.asyncio
async def test_dashboard_total_counts_a_synced_brokerage_once(
    session: AsyncSession, test_user, test_workspace
):
    await _seed_brokerage(session, test_user, test_workspace)
    summary = await get_summary(session, test_workspace.id, test_user.id)
    assert summary.total_balance.get("USD") == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_account_balance_stands_in_before_holdings_have_history(
    session: AsyncSession, test_user, test_workspace
):
    """Holdings only have values from their first sync. Earlier snapshots
    would otherwise lose the whole portfolio (#1146)."""
    today = date.today()
    account, _ = await _seed_brokerage(
        session, test_user, test_workspace, value_date=today - timedelta(days=5)
    )
    before = today - timedelta(days=10)
    assert await accounts_superseded_by_assets(session, test_workspace.id, before) == set()
    assert await accounts_superseded_by_assets(session, test_workspace.id, today) == {account.id}

    earlier = await _net_worth_at(session, test_workspace.id, before, "USD")
    assert earlier.breakdowns == {"accounts": 1000.0, "assets": 0.0, "liabilities": 0.0}
    now = await _net_worth_at(session, test_workspace.id, today, "USD")
    assert now.breakdowns == {"accounts": 0.0, "assets": 1000.0, "liabilities": 0.0}


@pytest.mark.asyncio
async def test_dated_purchase_price_counts_as_holding_history(
    session: AsyncSession, test_user, test_workspace
):
    """_net_worth_at falls back to a dated purchase price when no value is
    recorded yet; the guard must agree, or both copies land in that window."""
    today = date.today()
    account, asset = await _seed_brokerage(session, test_user, test_workspace)
    asset.purchase_price = Decimal("800.00")
    asset.purchase_date = today - timedelta(days=30)
    await session.commit()
    assert await accounts_superseded_by_assets(
        session, test_workspace.id, today - timedelta(days=20)
    ) == {account.id}
    assert await accounts_superseded_by_assets(
        session, test_workspace.id, today - timedelta(days=40)
    ) == set()


@pytest.mark.asyncio
async def test_archived_or_sold_holdings_do_not_supersede(
    session: AsyncSession, test_user, test_workspace
):
    account, asset = await _seed_brokerage(session, test_user, test_workspace, archived=True)
    assert await accounts_superseded_by_assets(session, test_workspace.id) == set()
    asset.is_archived = False
    asset.sell_date = date.today()
    await session.commit()
    assert await accounts_superseded_by_assets(session, test_workspace.id) == set()


@pytest.mark.asyncio
async def test_only_investment_accounts_qualify(
    session: AsyncSession, test_user, test_workspace
):
    """A checking account's balance is cash, not its wallet's positions."""
    await _seed_brokerage(session, test_user, test_workspace, account_type="checking")
    assert await accounts_superseded_by_assets(session, test_workspace.id) == set()


@pytest.mark.asyncio
async def test_manual_investment_account_still_counts(
    session: AsyncSession, test_user, test_workspace
):
    """A hand-made 401k with a balance and no synced holdings is not a copy."""
    acct = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="401k", type="investment", balance=Decimal("5000.00"), currency="USD",
    )
    session.add(acct)
    await session.flush()
    # A manual account's balance is its ledger, not the balance column.
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=acct.id, description="Contribution", amount=Decimal("5000.00"),
        date=date.today() - timedelta(days=1), type="credit", source="manual",
        currency="USD", created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    assert await accounts_superseded_by_assets(session, test_workspace.id) == set()
    dp = await _net_worth_at(session, test_workspace.id, date.today(), "USD")
    assert dp.breakdowns["accounts"] == 5000.0
