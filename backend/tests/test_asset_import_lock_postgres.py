"""The locks the order import takes, against real PostgreSQL.

SQLite runs one write at a time, so a test there cannot tell whether the locks
exist. Here the import is held open inside its own transaction and a second
session has to wait for it; removing the lock from the import turns these tests
red.
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.providers.market_price import MarketPriceProvider
from app.schemas.asset import MarketSymbolQuote
from app.services import asset_import_service, asset_transaction_service
from tests.test_asset_transaction_service import market_asset  # noqa: F401 -- same fixture


class QuietProvider(MarketPriceProvider):
    """Answers every quote request the same way; no network in this test."""

    async def search(self, query, limit=20):
        return []

    async def get_latest_prices(self, symbols):
        return {s.upper(): Decimal("100") for s in symbols}

    async def get_quote(self, symbol):
        return self._quote(symbol)

    async def get_quotes(self, symbols):
        return {s.upper(): self._quote(s) for s in symbols}

    @staticmethod
    def _quote(symbol: str) -> MarketSymbolQuote:
        return MarketSymbolQuote(
            symbol=symbol.upper(), name=f"{symbol.upper()} Inc", price=100.0,
            currency="USD", exchange="XNAS", quote_type="EQUITY", logo_url=None,
        )


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


def _orders(ticker: str, quantity: str):
    return asset_import_service.parse_orders_csv(
        f"ticker,date,quantity,price\n{ticker},2026-01-15,{quantity},100.00\n".encode()
    )[0]


def _hold_the_write_open(monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Pause the import after it has written, before it commits.

    The recompute runs once per touched holding, still inside the import's
    transaction, so gating it keeps the transaction — and its locks — open
    until the test releases it.
    """
    reached = asyncio.Event()
    release = asyncio.Event()
    real_recompute = asset_transaction_service.recompute_and_cache

    async def held(session_, asset):
        reached.set()
        await release.wait()
        await real_recompute(session_, asset)

    monkeypatch.setattr(
        asset_import_service.asset_transaction_service, "recompute_and_cache", held
    )
    return reached, release


@pytest.mark.asyncio
async def test_a_competing_write_waits_for_the_holding_lock(
    session, postgres_sessions, test_user, test_workspace, market_asset,  # noqa: F811
    monkeypatch,
):
    """The import locks the holding before it re-reads the ledger.

    While that transaction is open, a write that takes the same lock has to
    wait for it: the unit checks it runs afterwards are only safe because of
    that wait. A short `lock_timeout` makes the wait fail instead of hanging
    the suite.
    """
    market_asset.last_price = Decimal("100")
    await session.commit()
    reached, release = _hold_the_write_open(monkeypatch)

    task = asyncio.create_task(asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, _orders("PETR4.SA", "1"),
        market_provider=QuietProvider(),
    ))
    await asyncio.wait_for(reached.wait(), timeout=30)

    async with postgres_sessions() as other:
        await other.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(DBAPIError) as exc:
            await other.execute(
                select(Asset.id).where(Asset.id == market_asset.id).with_for_update()
            )

    assert "lock timeout" in str(exc.value).lower()

    release.set()
    summary = await asyncio.wait_for(task, timeout=30)
    assert summary["imported"] == 1


@pytest.mark.asyncio
async def test_a_second_import_of_the_same_new_ticker_waits_for_the_first(
    session, postgres_sessions, test_user, test_workspace, monkeypatch,
):
    """A holding the file creates cannot be locked as a row: it does not exist.

    Two imports would both find it missing and both create it, leaving two
    holdings of one position. The import locks the ticker instead, before it
    reads, so the second one waits here — and afterwards sees the holding the
    first one made.
    """
    ticker = f"NEW{uuid.uuid4().hex[:6].upper()}"
    reached, release = _hold_the_write_open(monkeypatch)

    task = asyncio.create_task(asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, _orders(ticker, "1"),
        market_provider=QuietProvider(),
    ))
    await asyncio.wait_for(reached.wait(), timeout=30)

    async with postgres_sessions() as other:
        await other.execute(text("SET LOCAL lock_timeout = '250ms'"))
        try:
            with pytest.raises(DBAPIError):
                # Bounded on purpose: without the ticker lock the second import
                # would reach the paused write and wait there forever, and
                # `lock_timeout` does not cover that wait.
                await asyncio.wait_for(
                    asset_import_service.import_orders(
                        other, test_workspace.id, test_user.id, _orders(ticker, "2"),
                        market_provider=QuietProvider(),
                    ),
                    timeout=15,
                )
        finally:
            # Released and joined whatever happened, so a failure here cannot
            # leave the first import holding its transaction open.
            release.set()
            assert (await asyncio.wait_for(task, timeout=30))["imported"] == 1

    result = await session.execute(
        select(Asset).where(
            Asset.workspace_id == test_workspace.id, Asset.ticker == ticker
        )
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_undo_waits_for_the_holding_lock(
    session, postgres_sessions, test_user, test_workspace, market_asset,  # noqa: F811
):
    """The undo locks the holdings before it checks what the ledger keeps.

    A sale that lands while the undo waits has to be part of the ledger it
    checks: otherwise the undo takes back the units that sale sold and leaves
    a position the ledger itself would have refused. The sale is written and
    committed while the undo is blocked here, so only a ledger re-read under
    the lock sees it — and the undo refuses instead of deleting.
    """
    from app.models.import_log import ImportLog

    market_asset.last_price = Decimal("100")
    await session.commit()
    summary = await asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, _orders("PETR4.SA", "1"),
        market_provider=QuietProvider(),
    )
    assert summary["imported"] == 1
    log = (await session.execute(select(ImportLog))).scalars().one()

    async with postgres_sessions() as other:
        await other.execute(
            select(Asset.id).where(Asset.id == market_asset.id).with_for_update()
        )
        undo = asyncio.create_task(
            asset_import_service.undo_import(session, test_workspace.id, log)
        )
        # Bounded on purpose, and shielded so the timeout only stops waiting:
        # the undo has to still be waiting while the lock is held, and run to
        # the end once it is released. `lock_timeout` cannot bound this side —
        # it belongs to the session that holds the lock, not to the one that
        # waits on it, and an unbounded wait here hung the suite in CI.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(undo), timeout=2)

        # The one unit the import bought, sold before the undo could read the
        # ledger. Committing is what releases the lock.
        other.add(AssetTransaction(
            asset_id=market_asset.id, workspace_id=test_workspace.id, kind="sell",
            quantity=Decimal("1"), price=Decimal("120"), fee=Decimal("0"),
            date=date(2026, 2, 1), source="manual",
        ))
        await other.commit()

    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(undo, timeout=30)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "import_undo_oversell"

    # Refused before anything was deleted, so the import is still there to undo.
    async with postgres_sessions() as fresh:
        assert (await fresh.execute(select(ImportLog))).scalars().first() is not None
