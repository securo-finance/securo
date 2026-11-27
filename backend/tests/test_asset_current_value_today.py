"""The Assets screen values an asset at today, like the dashboard and goals.

A valuation typed ahead of time (dated after today) has not happened yet:
the asset list, its detail and the response to an edit show the latest
valuation on or before today, the same number the dashboard's assets,
net worth and an asset goal use.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.user import User
from app.models.workspace import Workspace
from app.services.asset_service import get_asset_values_at


async def _asset_with_future_valuation(
    session: AsyncSession, user: User, workspace: Workspace
) -> Asset:
    today = app_today()
    asset = Asset(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name="Flat", type="real_estate", currency="BRL", valuation_method="manual",
        purchase_price=Decimal("800.00"), purchase_date=today - timedelta(days=30),
    )
    session.add(asset)
    session.add_all([
        AssetValue(
            id=uuid.uuid4(), asset_id=asset.id, workspace_id=workspace.id,
            amount=Decimal("1000.00"), date=today, source="manual",
        ),
        AssetValue(
            id=uuid.uuid4(), asset_id=asset.id, workspace_id=workspace.id,
            amount=Decimal("5000.00"), date=today + timedelta(days=400), source="manual",
        ),
    ])
    await session.commit()
    return asset


@pytest.mark.asyncio
async def test_assets_screen_leaves_out_a_future_valuation(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
):
    asset = await _asset_with_future_valuation(session, test_user, test_workspace)

    listed = await client.get("/api/assets", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    row = next(a for a in listed.json() if a["id"] == str(asset.id))
    assert row["current_value"] == 1000.0
    assert row["gain_loss"] == 200.0
    assert row["value_count"] == 2

    detail = await client.get(f"/api/assets/{asset.id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["current_value"] == 1000.0

    edited = await client.patch(
        f"/api/assets/{asset.id}", json={"name": "Flat downtown"}, headers=auth_headers
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["current_value"] == 1000.0

    # Same number as the dashboard and an asset goal.
    summary = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["assets_value"] == {"BRL": 1000.0}
    goal = await client.post(
        "/api/goals",
        json={
            "name": "Flat", "currency": "BRL", "target_amount": "5000",
            "tracking_type": "asset", "asset_id": str(asset.id),
        },
        headers=auth_headers,
    )
    assert goal.status_code == 201, goal.text
    assert Decimal(goal.json()["current_amount"]) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_only_future_valuations_fall_back_to_the_purchase_price(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
):
    today = app_today()
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Off-plan flat", type="real_estate", currency="BRL",
        valuation_method="manual", purchase_price=Decimal("800.00"),
        purchase_date=today - timedelta(days=10),
    )
    session.add(asset)
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
        amount=Decimal("5000.00"), date=today + timedelta(days=60), source="manual",
    ))
    await session.commit()

    detail = await client.get(f"/api/assets/{asset.id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["current_value"] == 800.0
    assert detail.json()["gain_loss"] == 0.0


@pytest.mark.asyncio
async def test_live_asset_totals_leave_out_a_future_valuation(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    await _asset_with_future_valuation(session, test_user, test_workspace)

    totals, _ = await get_asset_values_at(session, test_workspace.id, by_workspace=True)

    assert totals == {"BRL": 1000.0}
