import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.fx_rate import FxRate
from app.services.asset_group_service import get_group, get_groups


@pytest.mark.asyncio
async def test_get_groups_hides_empty_synced_wallets(
    session: AsyncSession, test_user, test_workspace
):
    active_connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="pluggy",
        external_id="item-active",
        institution_name="Active Bank",
        credentials={"token": "x"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(active_connection)

    manual_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Manual Wallet",
        source="manual",
    )
    orphan_synced_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="MeuPluggy",
        source="pluggy",
        connection_id=None,
    )
    active_synced_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Connected Wallet",
        source="pluggy",
        connection_id=active_connection.id,
    )
    session.add_all([manual_group, orphan_synced_group, active_synced_group])
    await session.commit()

    groups = await get_groups(session, test_workspace.id, test_user.id)
    names = {g.name for g in groups}

    assert "Manual Wallet" in names
    assert "Connected Wallet" not in names
    assert "MeuPluggy" not in names


@pytest.mark.asyncio
async def test_rollup_keeps_sub_cent_market_prices_in_primary_currency(
    session: AsyncSession, test_user, test_workspace
):
    """issue #622: a wallet holding low-priced foreign-currency assets must
    not roll up to 0.0 in the user's primary currency. Each converted amount
    keeps six decimals inside the sum; the read boundary still displays the
    total rounded to two. So 30 coins at 0.002087 USD x 0.92 (0.0576 EUR)
    must surface as 0.06, not the 0.0 the per-asset two-decimal quantization
    produced."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "EUR"}
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="EUR",
            date=date.today(),
            rate=Decimal("0.9200000000"),
            source="test",
        )
    )
    wallet = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Coin Wallet",
    )
    session.add(wallet)
    await session.commit()

    assets = [
        Asset(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            group_id=wallet.id,
            name=f"Coin {i}",
            type="investment",
            currency="USD",
            units=Decimal("1"),
            valuation_method="market_price",
            last_price=Decimal("0.002087"),
            ticker="COIN-USD",
            position=i,
        )
        for i in range(30)
    ]
    session.add_all(assets)
    await session.commit()
    # The rollup reads the latest AssetValue; the quote sync writes one per price.
    session.add_all(
        AssetValue(
            id=uuid.uuid4(),
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("0.002087"),
            date=date.today(),
            source="sync",
        )
        for asset in assets
    )
    await session.commit()

    read = await get_group(session, wallet.id, test_workspace.id, test_user.id)

    assert read is not None
    assert read.asset_count == 30
    assert read.current_value == pytest.approx(0.06)
    assert read.current_value_primary == pytest.approx(0.06)
@pytest.mark.asyncio
async def test_rollup_excludes_assets_without_fx_rate(
    session: AsyncSession, test_user, test_workspace
):
    """issue #574: a wallet holding a foreign-currency asset without a
    resolvable FX rate must not add the raw amount to the primary-currency
    total at a fake 1:1 rate. The asset still counts toward the wallet size;
    its primary contribution is 0. A sibling asset whose currency has a rate
    still converts."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    session.add(
        FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="EUR",
            date=date.today(),
            rate=Decimal("0.9200000000"),
            source="test",
        )
    )
    wallet = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Mixed Wallet",
    )
    session.add(wallet)
    await session.commit()

    eur_asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=wallet.id,
        name="Fund",
        type="investment",
        currency="EUR",
        units=Decimal("1000"),
        valuation_method="manual",
        purchase_price=Decimal("1000"),
        position=0,
    )
    gbp_asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=wallet.id,
        name="Cash",
        type="cash",
        currency="GBP",
        units=Decimal("500"),
        valuation_method="manual",
        purchase_price=Decimal("500"),
        position=1,
    )
    session.add_all([eur_asset, gbp_asset])
    await session.commit()
    session.add_all([
        AssetValue(
            id=uuid.uuid4(),
            asset_id=eur_asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("1000"),
            date=date.today(),
            source="manual",
        ),
        AssetValue(
            id=uuid.uuid4(),
            asset_id=gbp_asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("500"),
            date=date.today(),
            source="manual",
        ),
    ])
    await session.commit()

    read = await get_group(session, wallet.id, test_workspace.id, test_user.id)

    # 1000 EUR -> 1086.956522 USD at 1/0.92, shown at two decimals. The 500
    # GBP is left out of the primary total instead of being added as 500.0.
    assert read is not None
    assert read.asset_count == 2
    assert read.current_value_primary == pytest.approx(1086.96, abs=0.005)
