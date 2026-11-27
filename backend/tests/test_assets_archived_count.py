import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


async def _make_asset(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    is_archived: bool = False,
    group_id: uuid.UUID | None = None,
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        type="investment",
        currency="USD",
        valuation_method="manual",
        purchase_price=Decimal("100.00"),
        is_archived=is_archived,
        group_id=group_id,
        position=0,
    )
    session.add(asset)
    return asset


@pytest.mark.asyncio
async def test_archived_count_empty_workspace(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/assets/archived-count", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"count": 0, "by_wallet": {}, "ungrouped": 0}


@pytest.mark.asyncio
async def test_archived_count_groups_by_wallet_and_excludes_active(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    wallet_a = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Brokerage A",
    )
    wallet_b = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Brokerage B",
    )
    wallet_active_only = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Active Only",
    )
    session.add_all([wallet_a, wallet_b, wallet_active_only])
    await session.flush()

    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Archived A1",
        is_archived=True,
        group_id=wallet_a.id,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Archived A2",
        is_archived=True,
        group_id=wallet_a.id,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Active A",
        is_archived=False,
        group_id=wallet_a.id,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Archived B1",
        is_archived=True,
        group_id=wallet_b.id,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Active Only Holding",
        is_archived=False,
        group_id=wallet_active_only.id,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Archived Ungrouped",
        is_archived=True,
        group_id=None,
    )
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Active Ungrouped",
        is_archived=False,
        group_id=None,
    )
    await session.commit()

    response = await client.get("/api/assets/archived-count", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "count": 4,
        "by_wallet": {
            str(wallet_a.id): 2,
            str(wallet_b.id): 1,
        },
        "ungrouped": 1,
    }


@pytest.mark.asyncio
async def test_archived_count_enforces_workspace_isolation(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Other Workspace",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(other_workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=other_workspace.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    other_wallet = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Other Wallet",
    )
    session.add(other_wallet)
    await session.flush()

    await _make_asset(
        session,
        workspace_id=other_workspace.id,
        user_id=test_user.id,
        name="Other Archived Grouped",
        is_archived=True,
        group_id=other_wallet.id,
    )
    await _make_asset(
        session,
        workspace_id=other_workspace.id,
        user_id=test_user.id,
        name="Other Archived Ungrouped",
        is_archived=True,
        group_id=None,
    )
    await session.commit()

    primary_headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    other_headers = {**auth_headers, "X-Workspace-Id": str(other_workspace.id)}

    primary_resp = await client.get("/api/assets/archived-count", headers=primary_headers)
    assert primary_resp.status_code == 200
    assert primary_resp.json() == {"count": 0, "by_wallet": {}, "ungrouped": 0}

    other_resp = await client.get("/api/assets/archived-count", headers=other_headers)
    assert other_resp.status_code == 200
    assert other_resp.json() == {
        "count": 2,
        "by_wallet": {str(other_wallet.id): 1},
        "ungrouped": 1,
    }


@pytest.mark.asyncio
async def test_archived_count_access_token_scope(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    await _make_asset(
        session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Archived For Token",
        is_archived=True,
    )
    await session.commit()

    read_token_resp = await client.post(
        "/api/access-tokens",
        headers=auth_headers,
        json={"name": "assets-reader", "scopes": ["assets:read"]},
    )
    assert read_token_resp.status_code == 201
    read_token = read_token_resp.json()["token"]

    ok_resp = await client.get(
        "/api/assets/archived-count",
        headers={"Authorization": f"Bearer {read_token}"},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json() == {"count": 1, "by_wallet": {}, "ungrouped": 1}

    other_token_resp = await client.post(
        "/api/access-tokens",
        headers=auth_headers,
        json={"name": "accounts-only", "scopes": ["accounts:read"]},
    )
    assert other_token_resp.status_code == 201
    other_token = other_token_resp.json()["token"]

    denied_resp = await client.get(
        "/api/assets/archived-count",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied_resp.status_code == 403
