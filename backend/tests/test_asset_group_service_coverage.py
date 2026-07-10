"""Coverage-focused tests for asset_group_service.

Exercises the rollup math (incl. value-history fallback and FX conversion),
get/update/delete paths, position assignment, and the connection-sync
helpers (`ensure_group_for_connection`, `_unique_default_name`).
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.schemas.asset_group import AssetGroupCreate, AssetGroupUpdate
from app.services import asset_group_service as svc


async def _add_asset(
    session,
    user_id,
    workspace_id,
    group_id,
    *,
    currency="BRL",
    purchase_price=None,
    value_amount=None,
    is_archived=False,
    sell_date=None,
):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=f"Asset-{uuid.uuid4().hex[:5]}",
        type="investment",
        currency=currency,
        group_id=group_id,
        purchase_price=purchase_price,
        is_archived=is_archived,
        sell_date=sell_date,
    )
    session.add(asset)
    await session.flush()
    if value_amount is not None:
        session.add(
            AssetValue(
                id=uuid.uuid4(),
                asset_id=asset.id,
                workspace_id=workspace_id,
                amount=value_amount,
                date=date.today(),
                source="manual",
            )
        )
        await session.flush()
    return asset


@pytest.mark.asyncio
async def test_create_group_assigns_next_position(session: AsyncSession, test_user, test_workspace):
    g1 = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="W1")
    )
    g2 = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="W2")
    )
    # position defaulted to 0 -> _next_position appends.
    assert g2.position == g1.position + 1
    assert g1.source == "manual"


@pytest.mark.asyncio
async def test_create_group_honors_explicit_position(
    session: AsyncSession, test_user, test_workspace
):
    g = await svc.create_group(
        session,
        test_workspace.id,
        test_user.id,
        AssetGroupCreate(name="Pinned", position=7),
    )
    assert g.position == 7


@pytest.mark.asyncio
async def test_get_groups_rolls_up_values_and_fallback(
    session: AsyncSession, test_user, test_workspace
):
    g = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="Rollup")
    )
    # One asset with a value-history row (primary currency BRL).
    await _add_asset(
        session, test_user.id, test_workspace.id, g.id, currency="BRL", value_amount=Decimal("100")
    )
    # One asset with NO value history -> falls back to purchase_price.
    await _add_asset(
        session, test_user.id, test_workspace.id, g.id, currency="BRL", purchase_price=Decimal("50")
    )
    # Archived asset -> excluded.
    await _add_asset(
        session,
        test_user.id,
        test_workspace.id,
        g.id,
        currency="BRL",
        value_amount=Decimal("999"),
        is_archived=True,
    )
    # Sold asset -> excluded.
    await _add_asset(
        session,
        test_user.id,
        test_workspace.id,
        g.id,
        currency="BRL",
        value_amount=Decimal("999"),
        sell_date=date.today(),
    )
    # Asset with neither value nor purchase_price -> skipped silently.
    await _add_asset(session, test_user.id, test_workspace.id, g.id, currency="BRL")
    await session.commit()

    groups = await svc.get_groups(session, test_workspace.id, test_user.id)
    by_name = {x.name: x for x in groups}
    assert "Rollup" in by_name
    read = by_name["Rollup"]
    # asset_count counts non-archived, non-sold assets (3: two valued + one
    # with no value/price that is still counted). Sold/archived excluded.
    assert read.asset_count == 3
    assert read.current_value == 150.0
    # Primary currency is BRL (test_user pref) -> matches without conversion.
    assert read.current_value_primary == 150.0


@pytest.mark.asyncio
async def test_get_groups_converts_foreign_currency(
    session: AsyncSession, test_user, test_workspace, monkeypatch
):
    g = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="FX")
    )
    await _add_asset(
        session, test_user.id, test_workspace.id, g.id, currency="USD", value_amount=Decimal("10")
    )
    await session.commit()

    async def _fake_convert(session, amount, frm, to):
        return Decimal(amount) * Decimal("5"), Decimal("5")

    monkeypatch.setattr(svc, "convert", _fake_convert)

    groups = await svc.get_groups(session, test_workspace.id, test_user.id)
    read = {x.name: x for x in groups}["FX"]
    assert read.current_value == 10.0
    assert read.current_value_primary == 50.0


@pytest.mark.asyncio
async def test_get_groups_empty_returns_empty(session: AsyncSession, test_user, test_workspace):
    assert await svc.get_groups(session, test_workspace.id, test_user.id) == []


@pytest.mark.asyncio
async def test_get_groups_includes_institution_name(
    session: AsyncSession, test_user, test_workspace
):
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="pluggy",
        external_id="item-x",
        institution_name="Nubank",
        credentials={"t": "x"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    g = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced",
        source="pluggy",
        connection_id=conn.id,
    )
    session.add(g)
    await session.flush()
    await _add_asset(
        session, test_user.id, test_workspace.id, g.id, currency="BRL", value_amount=Decimal("1")
    )
    await session.commit()

    groups = await svc.get_groups(session, test_workspace.id, test_user.id)
    read = {x.name: x for x in groups}["Synced"]
    assert read.institution_name == "Nubank"


@pytest.mark.asyncio
async def test_get_group_found_and_not_found(session: AsyncSession, test_user, test_workspace):
    g = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="One")
    )
    found = await svc.get_group(session, g.id, test_workspace.id, test_user.id)
    assert found is not None
    assert found.name == "One"

    missing = await svc.get_group(session, uuid.uuid4(), test_workspace.id, test_user.id)
    assert missing is None


@pytest.mark.asyncio
async def test_update_group(session: AsyncSession, test_user, test_workspace):
    g = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="Old")
    )
    updated = await svc.update_group(
        session,
        g.id,
        test_workspace.id,
        test_user.id,
        AssetGroupUpdate(name="New", color="#000000"),
    )
    assert updated is not None
    assert updated.name == "New"
    assert updated.color == "#000000"

    none = await svc.update_group(
        session,
        uuid.uuid4(),
        test_workspace.id,
        test_user.id,
        AssetGroupUpdate(name="X"),
    )
    assert none is None


@pytest.mark.asyncio
async def test_delete_group(session: AsyncSession, test_user, test_workspace):
    g = await svc.create_group(
        session, test_workspace.id, test_user.id, AssetGroupCreate(name="Doomed")
    )
    assert await svc.delete_group(session, g.id, test_workspace.id) is True
    assert await svc.delete_group(session, g.id, test_workspace.id) is False


@pytest.mark.asyncio
async def test_ensure_group_for_connection_creates_then_relinks(session: AsyncSession, test_user):
    conn_id = uuid.uuid4()
    g = await svc.ensure_group_for_connection(
        session, test_user.id, conn_id, "pluggy", "ext-1", "MeuPluggy"
    )
    assert g.name == "MeuPluggy"
    assert g.connection_id == conn_id
    assert g.source == "pluggy"

    # Same external_id, new connection -> relink, keep name.
    new_conn = uuid.uuid4()
    g2 = await svc.ensure_group_for_connection(
        session, test_user.id, new_conn, "pluggy", "ext-1", "Renamed"
    )
    assert g2.id == g.id
    assert g2.connection_id == new_conn
    assert g2.name == "MeuPluggy"


@pytest.mark.asyncio
async def test_ensure_group_for_connection_matches_by_connection_id(
    session: AsyncSession, test_user
):
    conn_id = uuid.uuid4()
    g = await svc.ensure_group_for_connection(
        session, test_user.id, conn_id, "pluggy", None, "NoExtId"
    )
    g2 = await svc.ensure_group_for_connection(
        session, test_user.id, conn_id, "pluggy", None, "Other"
    )
    assert g2.id == g.id


@pytest.mark.asyncio
async def test_ensure_group_for_connection_disambiguates_name(session: AsyncSession, test_user):
    # Two distinct external ids, same default name -> " 2" suffix.
    g1 = await svc.ensure_group_for_connection(
        session, test_user.id, uuid.uuid4(), "pluggy", "ext-a", "MeuPluggy"
    )
    g2 = await svc.ensure_group_for_connection(
        session, test_user.id, uuid.uuid4(), "pluggy", "ext-b", "MeuPluggy"
    )
    g3 = await svc.ensure_group_for_connection(
        session, test_user.id, uuid.uuid4(), "pluggy", "ext-c", "MeuPluggy"
    )
    assert g1.name == "MeuPluggy"
    assert g2.name == "MeuPluggy 2"
    assert g3.name == "MeuPluggy 3"
