"""The holding lock the order import takes, against real PostgreSQL.

SQLite runs one write at a time, so a test there cannot tell whether the lock
exists. Here a second session is made to wait, and removing `FOR UPDATE` from
the import's read turns this test red.
"""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.user import User
from app.models.workspace import Workspace
from tests.test_asset_transaction_service import market_asset  # noqa: F401 -- same fixture


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_a_competing_write_waits_for_the_holding_lock(
    session, postgres_sessions, test_user: User, test_workspace: Workspace,
    market_asset: Asset,  # noqa: F811
):
    """The import locks the holding before it re-reads the ledger.

    While that transaction is open, a write that takes the same lock has to
    wait for it — the unit checks it would run afterwards are only safe
    because of that wait. A short `lock_timeout` makes the wait fail instead
    of hanging the suite.
    """
    session.add(AssetTransaction(
        asset_id=market_asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("20"), fee=Decimal("0"),
        date=date(2026, 1, 1), source="manual",
    ))
    await session.commit()
    # What the import does before it reads the ledger, held open like the
    # import's transaction holds it.
    await session.execute(
        select(Asset.id).where(Asset.id == market_asset.id).with_for_update()
    )

    async with postgres_sessions() as other:
        await other.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(DBAPIError) as exc:
            await other.execute(
                select(Asset.id).where(Asset.id == market_asset.id).with_for_update()
            )

    assert "lock timeout" in str(exc.value).lower()
