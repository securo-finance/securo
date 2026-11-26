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
from sqlalchemy import select, text

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

    orders = []
    for _ in range(ROUNDS):
        orders = []
        while len(orders) < CONCURRENT:
            order = shuffler.sample(ids, len(ids))
            if order != ids:
                orders.append(order)
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
    saved = [group_id for group_id, _ in rows]
    # The last reorder to commit wins, so the saved order is one of the last
    # round's submissions, none of which is the order the wallets started in.
    assert saved != ids
    assert saved in orders


@pytest.mark.asyncio
async def test_reorder_keeps_database_order_for_tied_positions(
    session, test_user, test_workspace
):
    """Wallets that tie on position keep the order the database sorts them in.

    An ICU collation sorts "abc" before "ABC"; Python's code-point order puts
    "ABC" first. Reordering two of three tied wallets has to fill the slots
    the database shows, leaving "ABC" in the middle.
    """
    connection = await session.connection()
    schema = connection.sync_connection.get_execution_options()[
        "schema_translate_map"
    ][None]
    await session.execute(
        text(
            f'ALTER TABLE "{schema}".asset_groups '
            'ALTER COLUMN name TYPE varchar(100) COLLATE "und-x-icu"'
        )
    )
    await session.commit()

    by_name = {
        name: AssetGroup(
            user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, position=0,
        )
        for name in ("bbc", "ABC", "abc")
    }
    session.add_all(by_name.values())
    await session.commit()

    assert await asset_group_service.reorder_groups(
        session, test_workspace.id, [by_name["bbc"].id, by_name["abc"].id]
    )

    names = (
        await session.execute(
            select(AssetGroup.name)
            .where(AssetGroup.workspace_id == test_workspace.id)
            .order_by(AssetGroup.position)
        )
    ).scalars().all()
    assert names == ["bbc", "ABC", "abc"]


@pytest.mark.asyncio
async def test_reorder_leaves_wallets_created_after_the_lock_alone(
    session, postgres_sessions, test_user, test_workspace
):
    """A wallet committed between the lock and the read is not renumbered.

    Renumbering it would need a row lock the reorder never took, which another
    reorder may already hold, and the two would deadlock.
    """
    first = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id, name="First", position=0
    )
    second = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Second", position=2
    )
    session.add_all([first, second])
    await session.commit()

    created = []
    execute = session.execute

    async def lock_then_create(statement, *args, **kwargs):
        result = await execute(statement, *args, **kwargs)
        if not created:
            async with postgres_sessions() as other:
                late = AssetGroup(
                    user_id=test_user.id, workspace_id=test_workspace.id,
                    name="Late", position=3,
                )
                other.add(late)
                await other.commit()
                created.append(late.id)
        return result

    session.execute = lock_then_create
    try:
        assert await asset_group_service.reorder_groups(
            session, test_workspace.id, [second.id, first.id]
        )
    finally:
        del session.execute

    async with postgres_sessions() as check:
        rows = (
            await check.execute(
                select(AssetGroup.name, AssetGroup.position).where(
                    AssetGroup.workspace_id == test_workspace.id
                )
            )
        ).all()
    assert dict(rows) == {"Second": 0, "First": 1, "Late": 3}
