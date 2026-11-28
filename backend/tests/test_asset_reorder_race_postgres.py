"""Reordering a wallet's holdings from two places at once, against real PostgreSQL.

Holdings are locked like wallets (#1944): in id order, so overlapping reorders
of the same wallet queue up instead of deadlocking, and the slots are read in
the database's own order.
"""
import asyncio
import random

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.services import asset_service

HOLDINGS = 8
CONCURRENT = 10
ROUNDS = 4


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def _wallet(session, test_user, test_workspace) -> AssetGroup:
    wallet = AssetGroup(user_id=test_user.id, workspace_id=test_workspace.id, name="Brokerage")
    session.add(wallet)
    await session.commit()
    return wallet


def _holding(test_user, test_workspace, wallet, name, position=0) -> Asset:
    return Asset(
        user_id=test_user.id, workspace_id=test_workspace.id, group_id=wallet.id,
        name=name, type="investment", currency="USD", position=position,
    )


@pytest.mark.asyncio
async def test_overlapping_reorders_all_succeed(
    session, postgres_sessions, test_user, test_workspace
):
    wallet = await _wallet(session, test_user, test_workspace)
    holdings = [
        _holding(test_user, test_workspace, wallet, f"Holding {index}", position=index + 1)
        for index in range(HOLDINGS)
    ]
    session.add_all(holdings)
    await session.commit()
    ids = [holding.id for holding in holdings]
    shuffler = random.Random(1941)

    async def reorder(order):
        async with postgres_sessions() as own:
            return await asset_service.reorder_assets(own, test_workspace.id, order)

    orders = []
    for _ in range(ROUNDS):
        orders = []
        while len(orders) < CONCURRENT:
            order = shuffler.sample(ids, len(ids))
            if order != ids:
                orders.append(order)
        results = await asyncio.gather(*(reorder(order) for order in orders), return_exceptions=True)
        failures = [result for result in results if result is not True]
        assert failures == [], failures

    async with postgres_sessions() as check:
        rows = (
            await check.execute(
                select(Asset.id, Asset.position)
                .where(Asset.group_id == wallet.id)
                .order_by(Asset.position)
            )
        ).all()
    assert [position for _, position in rows] == list(range(1, HOLDINGS + 1))
    saved = [asset_id for asset_id, _ in rows]
    assert saved != ids
    assert saved in orders


@pytest.mark.asyncio
async def test_reorder_keeps_database_order_for_tied_holdings(session, test_user, test_workspace):
    """Unplaced holdings that tie keep the order the database sorts their names in.

    An ICU collation sorts "abc" before "ABC"; Python's code-point order puts
    "ABC" first. Reordering two of three has to fill the slots the database
    shows, leaving "ABC" in the middle.
    """
    connection = await session.connection()
    schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]
    await session.execute(
        text(f'ALTER TABLE "{schema}".assets ALTER COLUMN name TYPE varchar(255) COLLATE "und-x-icu"')
    )
    await session.commit()

    wallet = await _wallet(session, test_user, test_workspace)
    by_name = {name: _holding(test_user, test_workspace, wallet, name) for name in ("bbc", "ABC", "abc")}
    session.add_all(by_name.values())
    await session.commit()

    assert await asset_service.reorder_assets(
        session, test_workspace.id, [by_name["bbc"].id, by_name["abc"].id]
    )

    names = (
        await session.execute(
            select(Asset.name).where(Asset.group_id == wallet.id).order_by(Asset.position)
        )
    ).scalars().all()
    assert names == ["bbc", "ABC", "abc"]
