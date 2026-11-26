"""Two imports into the same account at once, against real PostgreSQL (#613).

SQLite runs one write at a time, so it cannot show the deadlock: the import
log's foreign key takes a key-share lock on the account row, and two imports
that both held it and then asked for the update lock deadlocked, with
Postgres aborting one of them (a 500 at the API). The account is now locked
before the log is written, so the second import waits for the first.
"""
import asyncio
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.import_log import ImportLog
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services import import_service


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


def _batch(tag: str, rows: int = 3) -> list[TransactionImport]:
    return [
        TransactionImport(
            description=f"Batch {tag} row {i}",
            amount=Decimal(f"{10 + i}.{len(tag):02d}"),
            date=date(2026, 3, 1 + i),
            type="debit",
        )
        for i in range(rows)
    ]


def _meet_before_writing(monkeypatch, parties: int) -> None:
    """Hold every import at its first step until `parties` of them got there.

    The category check runs before anything is locked or written, so the
    imports leave it together and race for the account from the same point:
    the interleaving that deadlocked every time, without relying on timing.
    """
    arrived = 0
    everyone = asyncio.Event()
    real_check = import_service._ensure_categories_in_workspace

    async def meet(*args, **kwargs):
        nonlocal arrived
        await real_check(*args, **kwargs)
        arrived += 1
        if arrived >= parties:
            everyone.set()
        await asyncio.wait_for(everyone.wait(), timeout=30)

    monkeypatch.setattr(import_service, "_ensure_categories_in_workspace", meet)


async def _import(postgres_sessions, workspace_id, user_id, account_id, tag):
    async with postgres_sessions() as own:
        return await import_service.import_transactions(
            own, workspace_id, user_id, account_id, _batch(tag), "import",
            filename=f"{tag}.csv", detected_format="csv",
        )


async def _counts(postgres_sessions, account_id) -> tuple[int, int]:
    async with postgres_sessions() as fresh:
        rows = await fresh.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)
        )
        logs = await fresh.scalar(
            select(func.count()).select_from(ImportLog).where(ImportLog.account_id == account_id)
        )
    return rows or 0, logs or 0


@pytest.mark.asyncio
async def test_two_imports_into_one_account_both_land(
    postgres_sessions, test_user, test_workspace, test_account, monkeypatch,
):
    """Ten rounds of a pair started together: every import lands, none is aborted."""
    for round_ in range(10):
        # A fresh meeting point each round: the event of the last one is set.
        _meet_before_writing(monkeypatch, parties=2)
        results = await asyncio.wait_for(
            asyncio.gather(
                _import(postgres_sessions, test_workspace.id, test_user.id, test_account.id, f"a{round_}"),
                _import(postgres_sessions, test_workspace.id, test_user.id, test_account.id, f"b{round_}"),
            ),
            timeout=60,
        )
        assert [imported for imported, *_ in results] == [3, 3]

    assert await _counts(postgres_sessions, test_account.id) == (60, 20)


@pytest.mark.asyncio
async def test_ten_imports_at_once_all_land(
    postgres_sessions, test_user, test_workspace, test_account, monkeypatch,
):
    """Ten batches in parallel on one account: all of them land, rows add up."""
    _meet_before_writing(monkeypatch, parties=10)
    results = await asyncio.wait_for(
        asyncio.gather(*(
            _import(postgres_sessions, test_workspace.id, test_user.id, test_account.id, f"p{i}")
            for i in range(10)
        )),
        timeout=120,
    )
    assert all(imported == 3 for imported, *_ in results)
    assert await _counts(postgres_sessions, test_account.id) == (30, 10)
