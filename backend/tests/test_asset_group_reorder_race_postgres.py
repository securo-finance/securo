"""Reordering wallets from two places at once, against real PostgreSQL.

SQLite runs one write at a time, so it cannot deadlock. In PostgreSQL each
reorder locks every wallet of the workspace; when those locks were taken in
position order, a reorder that read the positions before another one committed
and a reorder that read them after locked the same rows in different orders,
and PostgreSQL killed one of them with a deadlock (a 500 on the API).
"""
import asyncio
import random

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.asset_group import AssetGroup
from app.services import asset_group_service

WALLETS = 8
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


@pytest.mark.asyncio
async def test_overlapping_reorders_all_succeed(
    session, postgres_sessions, test_user, test_workspace
):
    groups = [
        AssetGroup(
            user_id=test_user.id, workspace_id=test_workspace.id,
            name=f"Wallet {index}", position=index,
        )
        for index in range(WALLETS)
    ]
    session.add_all(groups)
    await session.commit()
    ids = [group.id for group in groups]
    shuffler = random.Random(637)

    async def reorder(order):
        async with postgres_sessions() as own:
            return await asset_group_service.reorder_groups(own, test_workspace.id, order)

    for _ in range(ROUNDS):
        orders = [shuffler.sample(ids, len(ids)) for _ in range(CONCURRENT)]
        results = await asyncio.gather(
            *(reorder(order) for order in orders), return_exceptions=True
        )
        failures = [result for result in results if result is not True]
        assert failures == [], failures

    async with postgres_sessions() as check:
        rows = (
            await check.execute(
                select(AssetGroup.id, AssetGroup.position)
                .where(AssetGroup.workspace_id == test_workspace.id)
                .order_by(AssetGroup.position)
            )
        ).all()
    assert [position for _, position in rows] == list(range(WALLETS))
    assert {group_id for group_id, _ in rows} == set(ids)
