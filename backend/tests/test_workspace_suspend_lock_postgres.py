"""A suspension waits for a job that already holds the workspace (ADR 0052).

SQLite has no row locks, so there a suspension cannot land in the middle of a
job. Here the job is paused before it finishes writing, and an administrator
suspends the workspace from another session. The suspension has to wait for
the job's commit; when the hold ends early (no `hold_active`, or a commit in
the middle of the run) it commits at once and the job then writes into a
suspended workspace.
"""

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import func, select, text

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import Asset
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.market_price import LatestClose, MarketPriceProvider
from app.schemas.asset import MarketSymbolMatch, MarketSymbolQuote
from app.services import (
    asset_service,
    connection_service,
    fx_rate_service,
    recurring_transaction_service,
    workspace_status_service,
)
from tests.test_workspace_status import _bank, _bank_provider


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


class _Closes(MarketPriceProvider):
    """A feed that answers the batch with one close."""

    name = "fake"

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_latest_closes(self, symbols: list[str]) -> dict[str, Optional[LatestClose]]:
        return {s.upper(): LatestClose(Decimal("42.00"), date(2026, 11, 26)) for s in symbols}

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        return None


def _user(name, currency="BRL", **over):
    return User(
        id=uuid.uuid4(), email=f"{name}@example.invalid", hashed_password="unused",
        is_active=True, is_verified=True, preferences={"currency_display": currency}, **over,
    )


async def _waits_or_ends(sessions, task):
    """Return once the task ended or some session waits on a lock.

    A fixed sleep reads a slow runner as a suspension that waited."""
    async with sessions() as probe:
        for _ in range(300):
            if task.done():
                return
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            )
            await probe.rollback()
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the suspension neither ended nor waited on a lock")


def _suspend(sessions, workspace_id, admin_id):
    async def run():
        async with sessions() as admin:
            await workspace_status_service.set_status(
                admin, workspace_id, "suspended", actor_id=admin_id, reason="Unpaid invoice",
            )
    return asyncio.create_task(run())


def _pause_after_the_rate_fetch(monkeypatch):
    """Serve a day's rates without the provider and pause once they are stored."""
    reached, release = asyncio.Event(), asyncio.Event()
    real_sync_rates = fx_rate_service.sync_rates

    async def rates(_provider, _target):
        return {"BRL": Decimal("5.00"), "USD": Decimal("1")}

    async def stored_then_paused(session, target_date=None):
        count = await real_sync_rates(session, target_date)
        reached.set()
        await release.wait()
        return count

    fx_rate_service.reset_fx_failure_cache()
    monkeypatch.setattr(fx_rate_service, "_fetch", rates)
    monkeypatch.setattr(fx_rate_service, "sync_rates", stored_then_paused)
    return reached, release


async def _seed(sessions):
    owner, admin = _user("owner"), _user("admin", is_superuser=True)
    workspace = Workspace(id=uuid.uuid4(), name="Held")
    async with sessions() as session:
        session.add_all([owner, admin, workspace])
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        asset = Asset(
            id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id,
            name="Petrobras", type="stock", currency="BRL",
            valuation_method="market_price", ticker="PETR4.SA", last_price=Decimal("30.00"),
        )
        session.add(asset)
        await session.commit()
    return workspace.id, admin.id, asset.id


@pytest.mark.asyncio
async def test_a_suspension_waits_for_the_price_sweep_that_holds_the_workspace(
    postgres_sessions, clean_db, monkeypatch,
):
    workspace_id, admin_id, asset_id = await _seed(postgres_sessions)
    reached, release = asyncio.Event(), asyncio.Event()
    real_split_dates = asset_service._last_split_dates

    async def paused(session, asset_ids):
        # Runs after the holdings were re-read and locked, before any write.
        reached.set()
        await release.wait()
        return await real_split_dates(session, asset_ids)

    monkeypatch.setattr(asset_service, "_last_split_dates", paused)

    async with postgres_sessions() as sweep, postgres_sessions() as admin:
        sweeping = asyncio.create_task(
            asset_service.refresh_all_market_prices(sweep, market_provider=_Closes())
        )
        await asyncio.wait_for(reached.wait(), timeout=30)

        suspending = asyncio.create_task(
            workspace_status_service.set_status(
                admin, workspace_id, "suspended", actor_id=admin_id, reason="Unpaid invoice",
            )
        )
        try:
            await _waits_or_ends(postgres_sessions, suspending)
            # The sweep holds the workspace active until it commits.
            assert not suspending.done()
        finally:
            release.set()
            summary = await asyncio.wait_for(sweeping, timeout=30)
            await asyncio.wait_for(suspending, timeout=30)

    assert summary["refreshed"] == 1
    async with postgres_sessions() as later:
        price = await later.scalar(select(Asset.last_price).where(Asset.id == asset_id))
        status = await later.scalar(select(Workspace.status).where(Workspace.id == workspace_id))
    # The price written belongs to the moment before the suspension.
    assert price == Decimal("42.00")
    assert status == "suspended"


@pytest.mark.asyncio
async def test_a_rate_fetched_for_a_bill_keeps_the_workspace_held(
    postgres_sessions, clean_db, monkeypatch,
):
    """A bill in a currency with no stored rate fetches one in the middle of the
    run. Storing it must not commit the job: that would end the hold while the
    loop still writes."""
    owner, admin = _user("owner"), _user("admin", is_superuser=True)
    workspace = Workspace(id=uuid.uuid4(), name="Held")
    today = app_today()
    async with postgres_sessions() as session:
        session.add_all([owner, admin, workspace])
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        account = Account(
            id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id,
            name="Dollars", type="checking", currency="USD", balance=Decimal("0"),
        )
        session.add(account)
        await session.flush()
        session.add(RecurringTransaction(
            id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id,
            account_id=account.id, description="Hosting", amount=Decimal("10.00"),
            currency="USD", type="debit", frequency="monthly",
            start_date=today, next_occurrence=today, auto_generate=True, is_active=True,
        ))
        await session.commit()
    reached, release = _pause_after_the_rate_fetch(monkeypatch)

    async with postgres_sessions() as job:
        generating = asyncio.create_task(
            recurring_transaction_service.generate_pending(job, owner.id)
        )
        await asyncio.wait_for(reached.wait(), timeout=30)
        suspending = _suspend(postgres_sessions, workspace.id, admin.id)
        try:
            await _waits_or_ends(postgres_sessions, suspending)
            assert not suspending.done()
        finally:
            release.set()
            generated = await asyncio.wait_for(generating, timeout=30)
            await asyncio.wait_for(suspending, timeout=30)

    assert generated == 1
    async with postgres_sessions() as later:
        row = (await later.execute(
            select(Transaction.amount_primary).where(Transaction.workspace_id == workspace.id)
        )).one()
        rate = await later.scalar(
            select(FxRate.rate).where(FxRate.quote_currency == "BRL", FxRate.date == today)
        )
    # The job's own commit stored the rate it fetched and the row it converted.
    assert rate == Decimal("5.00")
    assert row.amount_primary == Decimal("50.00")


@pytest.mark.asyncio
async def test_a_rate_fetched_during_a_sync_commits_nothing_early(
    postgres_sessions, clean_db, monkeypatch,
):
    """A synced row in a currency with no stored rate fetches one before the
    run's final hold. Storing it must not commit the rows read so far: a
    suspension landing then would leave half a sync in a suspended workspace."""
    owner, admin = _user("owner", currency="USD"), _user("admin", is_superuser=True)
    workspace = Workspace(id=uuid.uuid4(), name="Held")
    async with postgres_sessions() as session:
        session.add_all([owner, admin, workspace])
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        connection = _bank(owner, workspace.id)
        session.add(connection)
        await session.commit()
    reached, release = _pause_after_the_rate_fetch(monkeypatch)
    monkeypatch.setattr(connection_service, "detect_transfer_pairs", _no_pairs)

    raised = None
    async with postgres_sessions() as job:
        syncing = asyncio.create_task(connection_service.sync_connection(
            job, connection.id, workspace.id, owner.id, provider=_bank_provider(),
        ))
        await asyncio.wait_for(reached.wait(), timeout=30)
        suspending = _suspend(postgres_sessions, workspace.id, admin.id)
        try:
            await _waits_or_ends(postgres_sessions, suspending)
        finally:
            release.set()
            try:
                await asyncio.wait_for(syncing, timeout=30)
            except connection_service.WorkspaceSuspendedError as exc:
                raised = exc
            await asyncio.wait_for(suspending, timeout=30)

    async with postgres_sessions() as later:
        rows = await later.scalar(
            select(func.count()).select_from(Transaction)
            .where(Transaction.workspace_id == workspace.id)
        )
    # All or nothing: a sync the suspension stopped kept no row.
    if raised is not None:
        assert rows == 0
    else:
        assert rows > 0


async def _no_pairs(*_args, **_kwargs):
    return []
