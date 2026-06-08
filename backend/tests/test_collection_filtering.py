"""Collection account-filter (issue #105) — verifies dashboard & reports
aggregations scope to the active collection's accounts.

The contract: passing ``account_ids`` restricts balances and P&L to those
accounts. Assets are excluded (collections hold accounts only) and the
unfiltered path is unchanged.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.transaction import Transaction
from app.models.user import User
from app.services import dashboard_service, report_service


async def _account(session, user, name) -> Account:
    acc = Account(
        id=uuid.uuid4(), user_id=user.id, name=name, type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _txn(session, user, account, amount, typ):
    session.add(Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id,
        description="t", amount=Decimal(str(amount)),
        date=date.today().replace(day=min(5, date.today().day)),
        type=typ, source="manual", created_at=datetime.now(timezone.utc),
    ))
    await session.commit()


@pytest.mark.asyncio
async def test_summary_balances_and_pnl_filter_by_collection(
    session: AsyncSession, test_user: User, test_workspace
):
    a = await _account(session, test_user, "A")
    b = await _account(session, test_user, "B")
    # A: +1000 income, -100 expense → balance 900. B: +500 income → balance 500.
    await _txn(session, test_user, a, 1000, "credit")
    await _txn(session, test_user, a, 100, "debit")
    await _txn(session, test_user, b, 500, "credit")

    ws = test_workspace.id
    all_ = await dashboard_service.get_summary(session, ws, test_user.id)
    only_a = await dashboard_service.get_summary(session, ws, test_user.id, account_ids=[a.id])
    only_b = await dashboard_service.get_summary(session, ws, test_user.id, account_ids=[b.id])

    # Unfiltered = both accounts.
    assert all_.total_balance.get("BRL") == 1400.0
    assert all_.monthly_income == 1500.0
    assert all_.monthly_expenses == 100.0

    # Filtered to A.
    assert only_a.total_balance.get("BRL") == 900.0
    assert only_a.monthly_income == 1000.0
    assert only_a.monthly_expenses == 100.0
    assert only_a.accounts_count == 1

    # Filtered to B.
    assert only_b.total_balance.get("BRL") == 500.0
    assert only_b.monthly_income == 500.0
    assert only_b.monthly_expenses == 0.0


@pytest.mark.asyncio
async def test_empty_collection_yields_zero(
    session: AsyncSession, test_user: User, test_workspace
):
    a = await _account(session, test_user, "A")
    await _txn(session, test_user, a, 1000, "credit")

    empty = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, account_ids=[]
    )
    assert empty.total_balance.get("BRL", 0.0) in (0.0, None) or empty.total_balance == {}
    assert empty.monthly_income == 0.0
    assert empty.accounts_count == 0


@pytest.mark.asyncio
async def test_net_worth_excludes_assets_when_filtered(
    session: AsyncSession, test_user: User, test_workspace
):
    a = await _account(session, test_user, "A")
    await _txn(session, test_user, a, 1000, "credit")  # balance 1000

    # An asset that should count toward unfiltered net worth but be excluded
    # under a collection filter.
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="House", type="real_estate", currency="BRL",
        purchase_price=Decimal("5000"), purchase_date=date.today().replace(day=1),
        valuation_method="manual",
    ))
    await session.commit()

    ws = test_workspace.id
    unfiltered = await report_service.get_net_worth_report(session, ws, test_user.id)
    filtered = await report_service.get_net_worth_report(session, ws, test_user.id, account_ids=[a.id])

    # Asset present unfiltered, gone when filtered.
    assert unfiltered.summary.breakdowns[1].key == "assets"
    assert unfiltered.summary.breakdowns[1].value > 0
    assert filtered.summary.breakdowns[1].value == 0
    # Composition under the filter has no asset items.
    assert all(item.group != "assets" for item in filtered.composition)


@pytest.mark.asyncio
async def test_net_worth_includes_collection_wallet_assets(
    session: AsyncSession, test_user: User, test_workspace
):
    """A collection with a wallet includes that wallet's assets in net worth,
    but not assets outside the wallet."""
    a = await _account(session, test_user, "A")
    await _txn(session, test_user, a, 1000, "credit")  # account balance 1000

    wallet = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Investments",
    )
    session.add(wallet)
    await session.commit()
    # Asset inside the wallet (counts) vs. one outside (excluded).
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="In wallet", type="investment", currency="BRL", group_id=wallet.id,
        purchase_price=Decimal("3000"), purchase_date=date.today().replace(day=1),
        valuation_method="manual",
    ))
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Outside", type="investment", currency="BRL", group_id=None,
        purchase_price=Decimal("9999"), purchase_date=date.today().replace(day=1),
        valuation_method="manual",
    ))
    await session.commit()

    ws = test_workspace.id
    # Collection = account A + the wallet.
    rep = await report_service.get_net_worth_report(
        session, ws, test_user.id, account_ids=[a.id], asset_group_ids=[wallet.id]
    )
    assets_bd = next(b for b in rep.summary.breakdowns if b.key == "assets")
    assert assets_bd.value == 3000.0  # only the in-wallet asset, not the 9999 one
    assert rep.summary.breakdowns[0].value == 1000.0  # account A
    # Net worth = account + wallet asset.
    assert rep.summary.primary_value == 4000.0
    # Dashboard summary mirrors it (assets = wallet asset only).
    summ = await dashboard_service.get_summary(
        session, ws, test_user.id, account_ids=[a.id], asset_group_ids=[wallet.id]
    )
    assert summ.assets_value_primary == 3000.0
    # total_balance includes asset values by design: account 1000 + wallet 3000.
    assert summ.total_balance.get("BRL") == 4000.0


@pytest.mark.asyncio
async def test_wallet_only_collection_shows_assets_no_accounts(
    session: AsyncSession, test_user: User, test_workspace
):
    """A wallet-only collection (no accounts) filters accounts to none and
    shows only the wallet's assets — driven by asset_group_ids with
    account_ids omitted, the way the frontend sends it."""
    # An account with a balance that must NOT appear (it's not in the collection).
    other = await _account(session, test_user, "Not in collection")
    await _txn(session, test_user, other, 7777, "credit")

    wallet = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Crypto",
    )
    session.add(wallet)
    await session.commit()
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="BTC", type="crypto", currency="BRL", group_id=wallet.id,
        purchase_price=Decimal("2500"), purchase_date=date.today().replace(day=1),
        valuation_method="manual",
    ))
    await session.commit()

    ws = test_workspace.id
    # Frontend sends asset_group_ids only (empty account list isn't transmittable);
    # the service coerces accounts to empty so the 7777 account is excluded.
    summ = await dashboard_service.get_summary(
        session, ws, test_user.id, asset_group_ids=[wallet.id]
    )
    assert summ.accounts_count == 0
    assert summ.monthly_income == 0.0
    assert summ.assets_value_primary == 2500.0
    assert summ.total_balance.get("BRL") == 2500.0  # only the wallet asset

    rep = await report_service.get_net_worth_report(
        session, ws, test_user.id, asset_group_ids=[wallet.id]
    )
    assert rep.summary.breakdowns[0].value == 0.0  # accounts
    assets_bd = next(b for b in rep.summary.breakdowns if b.key == "assets")
    assert assets_bd.value == 2500.0
    assert rep.summary.primary_value == 2500.0
