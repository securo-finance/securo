"""Bounded SQLite evidence for recent-list input scale, not a PG performance gate."""
import json
import resource
import sys
from datetime import date, timedelta
from decimal import Decimal
from time import perf_counter
from uuid import UUID

import pytest
from sqlalchemy import event

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import recurring_price_change_service as service


@pytest.mark.parametrize("series_count", [10, 100, 1000])
async def test_recent_price_changes_many_series_exact_latest(
    session, test_user, test_workspace, test_account, monkeypatch, series_count
):
    """Output limits must not truncate input or lose late-series/tied events.

    Twenty posted rows per series: an early change at index 2, an identical
    duplicate at 4/5, conflicting amounts at 8/9, and a known latest change
    at 12 or 19. The last series stays constant (no event). Expected evidence
    is authored from these fixture positions, never from a detector call.
    """
    setup_started = perf_counter()
    workspace_id = test_workspace.id
    expected = []
    series_rows = []
    transaction_rows = []
    for index in range(series_count):
        series_id = UUID(int=index + 1)
        start = date(2023, 1, 1) + timedelta(days=index // 2)
        latest_index = 12 if index % 5 == 0 else 19
        no_event = index == series_count - 1
        base = Decimal(10 + index % 80)
        series_rows.append(RecurringTransaction(
            id=series_id, user_id=test_user.id, workspace_id=workspace_id,
            account_id=test_account.id, description=f"Synthetic subscription {index}",
            amount=base, currency="BRL", type="debit", frequency="monthly",
            start_date=start, next_occurrence=start + timedelta(days=600),
        ))
        for position in range(20):
            day_index = {5: 4, 9: 8}.get(position, position)
            when = start + timedelta(days=30 * day_index)
            amount = base
            if not no_event:
                amount += Decimal(2 if position >= 2 else 0)
                if position == 9:
                    amount += Decimal(1)  # Ambiguous date resets the chain.
                if position >= latest_index:
                    amount += Decimal(3)
            transaction_rows.append(Transaction(
                id=UUID(int=100_000 + index * 20 + position),
                user_id=test_user.id, workspace_id=workspace_id,
                account_id=test_account.id, recurring_transaction_id=series_id,
                description=f"Synthetic posted subscription {index}",
                amount=amount, currency="BRL", type="debit", source="csv",
                status="posted", is_ignored=False, date=when, effective_date=when,
            ))
        if not no_event:
            expected.append((
                start + timedelta(days=30 * latest_index), series_id,
                UUID(int=100_000 + index * 20 + latest_index - 1),
                UUID(int=100_000 + index * 20 + latest_index),
            ))
    expected.sort(key=lambda item: (-item[0].toordinal(), item[1]))
    assert len({item[0] for item in expected}) < len(expected)  # Real date ties.
    assert expected[0][1] == UUID(int=series_count - 1)  # Near end of SQL input.
    session.add_all(list(reversed(series_rows)))
    await session.flush()
    session.add_all(list(reversed(transaction_rows)))
    await session.flush()
    # Do not time reads against an identity map retaining the seeded objects.
    session.expunge_all()
    del series_rows, transaction_rows
    setup_seconds = perf_counter() - setup_started

    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    assert connection.engine.url.database == ":memory:"
    counters = {"retrieved": 0, "processed": 0, "series": 0}
    statements = []
    execute = session.execute
    analyze = service.analyze_price_changes

    class CountedResult:
        def __init__(self, result):
            self.result = result

        def all(self):
            rows = self.result.all()
            counters["retrieved"] += len(rows)
            return rows

    async def counted_execute(*args, **kwargs):
        return CountedResult(await execute(*args, **kwargs))

    def counted_analysis(series, rows):
        counters["processed"] += len(rows)
        counters["series"] += 1
        return analyze(series, rows)

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(session, "execute", counted_execute)
    monkeypatch.setattr(service, "analyze_price_changes", counted_analysis)
    event.listen(connection.sync_connection, "before_cursor_execute", record_sql)
    try:
        # Interleave limits; first invocation is cold-ish, later calls reuse SQL
        # compilation/SQLite caches. Three samples are NOT a p95 benchmark.
        for sample in range(3):
            for limit in (1, 50):
                statements.clear()
                counters.update(retrieved=0, processed=0, series=0)
                started = perf_counter()
                recent = await service.get_recent_price_changes(session, workspace_id, limit)
                elapsed = perf_counter() - started
                actual = [(
                    item.current_date, item.recurring_id,
                    item.previous_transaction_id, item.current_transaction_id,
                ) for item in recent]
                assert actual == expected[:limit]
                assert len(statements) == 1
                assert statements[0].lstrip().upper().startswith("SELECT ")
                assert counters == {
                    "retrieved": series_count * 20,
                    "processed": series_count * 20,
                    "series": series_count,
                }
                assert not (session.new or session.dirty or session.deleted)
                peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                print("RECENT_SCALE " + json.dumps({
                    "series": series_count, "linked_posted_rows": series_count * 20,
                    "limit": limit, "sample": sample, "sql_count": len(statements),
                    **counters, "returned": len(recent), "seconds": elapsed,
                    "fixture_seconds": setup_seconds,
                    "process_peak_rss_bytes": peak_rss if sys.platform == "darwin"
                    else peak_rss * 1024,
                }, sort_keys=True))
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record_sql)
