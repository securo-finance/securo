"""The locks the order import takes, against real PostgreSQL.

SQLite runs one write at a time, so a test there cannot tell whether the locks
exist. Here the import is held open inside its own transaction and a second
session has to wait for it; removing the lock from the import turns these tests
red.
"""
import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models.asset import Asset
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
        with pytest.raises(DBAPIError):
            await asset_import_service.import_orders(
                other, test_workspace.id, test_user.id, _orders(ticker, "2"),
                market_provider=QuietProvider(),
            )

    release.set()
    assert (await asyncio.wait_for(task, timeout=30))["imported"] == 1

    result = await session.execute(
        select(Asset).where(
            Asset.workspace_id == test_workspace.id, Asset.ticker == ticker
        )
    )
    assert len(result.scalars().all()) == 1
