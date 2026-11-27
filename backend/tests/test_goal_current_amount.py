"""A tracked goal's current amount matches the screens it mirrors (#640).

A goal on a manual credit card reads the balance the Accounts screen shows,
with the debt's liability sign, and a wallet, asset or net worth goal values
its assets at today, leaving out a valuation dated in the future.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace


async def _create_goal(client: AsyncClient, headers: dict, **fields) -> dict:
    payload = {"name": "Goal", "currency": "BRL", **fields}
    response = await client.post("/api/goals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_manual_card_goal_reads_the_debt_the_accounts_screen_shows(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
):
    card = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Manual card",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(card)
    today = app_today()
    # A posted bill payment and authorized purchases still pending: the posted
    # rows alone add up to a credit, while the card owes 1,000.00.
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=card.id, description="Bill payment", amount=Decimal("2000.00"),
            date=today - timedelta(days=3), type="credit", status="posted", source="manual", currency="BRL",
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=card.id, description="Groceries", amount=Decimal("3000.00"),
            date=today - timedelta(days=1), type="debit", status="pending", source="manual", currency="BRL",
        ),
    ])
    await session.commit()

    listed = await client.get(f"/api/accounts/{card.id}", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["current_balance"] == -1000.0

    goal = await _create_goal(
        client, auth_headers, name="Pay off card", target_amount="0",
        tracking_type="account", account_id=str(card.id),
        target_date=(today + timedelta(days=200)).isoformat(),
    )

    assert Decimal(goal["current_amount"]) == Decimal("-1000.00")
    assert goal["percentage"] == 0.0
    assert goal["on_track"] != "achieved"


@pytest.mark.asyncio
async def test_asset_goals_leave_out_a_future_valuation(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
):
    today = app_today()
    group = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Long term", icon="wallet", color="#0EA5E9",
    )
    session.add(group)
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Fund", type="investment", currency="BRL",
        purchase_price=Decimal("800.00"), purchase_date=today - timedelta(days=30),
        group_id=group.id,
    )
    session.add(asset)
    session.add_all([
        AssetValue(
            id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
            amount=Decimal("1000.00"), date=today, source="manual",
        ),
        # Typed ahead of time: the end-of-month value, two days early.
        AssetValue(
            id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
            amount=Decimal("1500.00"), date=today + timedelta(days=2), source="manual",
        ),
    ])
    await session.commit()

    asset_goal = await _create_goal(
        client, auth_headers, target_amount="5000", tracking_type="asset",
        asset_id=str(asset.id),
    )
    wallet_goal = await _create_goal(
        client, auth_headers, target_amount="5000", tracking_type="asset_group",
        asset_group_id=str(group.id),
    )
    net_worth_goal = await _create_goal(
        client, auth_headers, target_amount="5000", tracking_type="net_worth",
    )

    assert Decimal(asset_goal["current_amount"]) == Decimal("1000.00")
    assert Decimal(wallet_goal["current_amount"]) == Decimal("1000.00")
    assert Decimal(net_worth_goal["current_amount"]) == Decimal("1000.00")

    # The net worth goal agrees with the dashboard's assets for today.
    summary = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["assets_value"] == {"BRL": 1000.0}
