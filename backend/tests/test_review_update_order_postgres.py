"""Ordered full-write-set PATCH locks, dirty guards and clear/review serialization."""
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionUpdate
from app.services import transaction_service
from tests.test_review_concurrency_postgres import schema_sessions as schema_sessions
from tests.test_review_writer_races_postgres import seed, stamp, wait_for_real_block

pytestmark = pytest.mark.asyncio


async def contend(factory, monkeypatch, ids, first, second):
    async with factory() as winner, factory() as loser, factory() as observer:
        # Strong stale refs survive the winner's commit.
        held = list((await loser.scalars(select(Transaction).where(
            Transaction.workspace_id == ids['workspace']))).all())
        a_pid = await winner.scalar(text('SELECT pg_backend_pid()'))
        b_pid = await loser.scalar(text('SELECT pg_backend_pid()'))
        ready, release = asyncio.Event(), asyncio.Event()
        commit = winner.commit
        statements = []
        connection = await winner.connection()

        def record(conn, cursor, statement, params, context, many):
            if 'FOR UPDATE' in statement:
                statements.append(statement)

        event.listen(connection.sync_connection, 'before_cursor_execute', record)

        async def hold():
            await winner.flush()
            ready.set()
            await release.wait()
            await commit()

        monkeypatch.setattr(winner, 'commit', hold)
        a = asyncio.create_task(first(winner))
        b = None
        try:
            ready_task = asyncio.create_task(ready.wait())
            done, _ = await asyncio.wait([a, ready_task], timeout=8, return_when=asyncio.FIRST_COMPLETED)
            if a in done and not ready.is_set():
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)
                await a
            await asyncio.wait_for(ready_task, 8)
            b = asyncio.create_task(second(loser))
            await wait_for_real_block(observer, b, b_pid, a_pid)
            release.set()
            await asyncio.wait_for(asyncio.gather(a, b), 8)
            assert held
        finally:
            release.set()
            for task in (a, b):
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        return statements


@pytest.mark.parametrize('kind', ['series', 'cascade'])
async def test_reversed_anchor_patch_locks_union_in_id_order(schema_sessions, monkeypatch, kind):
    ids, _ = await seed(schema_sessions, 'ordering')
    series_id, pair_id = uuid.uuid4(), uuid.uuid4()
    async with schema_sessions() as session:
        anchor = await session.get(Transaction, ids['x'])
        assert anchor is not None
        other = Transaction(id=ids['y'], user_id=ids['owner'], workspace_id=ids['workspace'],
            account_id=ids['account'], description='writer-regression', amount=Decimal('10'),
            currency='USD', date=date(2026, 10, 15), effective_date=date(2026, 10, 15),
            type='credit', source='import', review_status='needs_review')
        session.add(other)
        if kind == 'series':
            for row, number in [(anchor, 2), (other, 1)]:
                row.installment_series_id = series_id
                row.installment_number = number
                row.total_installments = 2
                row.installment_total_amount = Decimal('20')
                row.installment_purchase_date = date(2026, 10, 15)
        else:
            anchor.transfer_pair_id = other.transfer_pair_id = pair_id
        await session.commit()

    async def first(session):
        return await transaction_service.update_transaction(session, ids['y'], ids['workspace'], ids['a'],
            TransactionUpdate(description='First ordered change', amount=Decimal('11') if kind == 'series' else Decimal('10'),
                              apply_to='all', review_status='reviewed'))

    async def second(session):
        return await transaction_service.update_transaction(session, ids['x'], ids['workspace'], ids['b'],
            TransactionUpdate(description='Second ordered change', amount=Decimal('12') if kind == 'series' else Decimal('10'),
                              apply_to='all', review_status='reviewed'))

    statements = await contend(schema_sessions, monkeypatch, ids, first, second)
    assert len(statements) == 1  # anchor and siblings/counterpart acquired together
    assert re.search(r'ORDER BY [\w".]*transactions\.id FOR UPDATE', statements[0])
    async with schema_sessions() as verify:
        rows = (await verify.scalars(select(Transaction).where(Transaction.workspace_id == ids['workspace']))).all()
        assert len(rows) == 2
        expected_amount = Decimal('12') if kind == 'series' else Decimal('10')
        assert all(row.description == 'Second ordered change' and row.amount == expected_amount for row in rows)
        by_id = {row.id: row for row in rows}
        # Review never cascades: each anchor gets only its own human stamp.
        assert stamp(by_id[ids['x']])[2:] == (ids['b'], 'human', None)
        assert stamp(by_id[ids['y']])[2:] == (ids['a'], 'human', None)
        if kind == 'series':
            assert all(row.installment_total_amount == Decimal('24') for row in rows)


async def test_clear_serializes_after_review(schema_sessions, monkeypatch):
    ids, _ = await seed(schema_sessions, 'clear')
    async def first(session):
        return await transaction_service.review_transaction(session, ids['x'], ids['workspace'], ids['a'], 'reviewed')
    async def second(session):
        return await transaction_service.review_transaction(session, ids['x'], ids['workspace'], ids['b'], 'needs_review')
    await contend(schema_sessions, monkeypatch, ids, first, second)
    async with schema_sessions() as session:
        row = await session.get(Transaction, ids['x'])
        assert stamp(row) == ('needs_review', None, None, None, None)


async def test_membership_expansion_conflicts_without_partial_patch(schema_sessions):
    ids, _ = await seed(schema_sessions, 'membership')
    old_series, new_series = uuid.uuid4(), uuid.uuid4()
    async with schema_sessions() as session:
        anchor = await session.get(Transaction, ids['x'])
        assert anchor is not None
        anchor.installment_series_id = old_series
        anchor.installment_number = 1
        anchor.total_installments = 2
        anchor.installment_purchase_date = date(2026, 10, 15)
        session.add(Transaction(id=ids['z'], user_id=ids['owner'], workspace_id=ids['workspace'],
            account_id=ids['account'], description='New series member', amount=Decimal('10'),
            currency='USD', date=date(2026, 10, 15), effective_date=date(2026, 10, 15),
            type='credit', source='import', review_status='needs_review',
            installment_series_id=new_series, installment_number=2, total_installments=2,
            installment_purchase_date=date(2026, 10, 15)))
        await session.commit()
    async with schema_sessions() as holder, schema_sessions() as editor, schema_sessions() as observer:
        # Actual SQL fixture stimulus changes membership while PATCH waits for
        # its originally discovered union. No injected conflict or fake lock.
        anchor = await holder.get(Transaction, ids['x'])
        assert anchor is not None
        anchor.installment_series_id = new_series
        await holder.flush()
        a_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
        b_pid = await editor.scalar(text('SELECT pg_backend_pid()'))
        task = asyncio.create_task(transaction_service.update_transaction(
            editor, ids['x'], ids['workspace'], ids['a'],
            TransactionUpdate(description='Must roll back', amount=Decimal('50'), apply_to='all', review_status='reviewed')))
        try:
            await wait_for_real_block(observer, task, b_pid, a_pid)
            await holder.commit()
            with pytest.raises(HTTPException) as caught:
                await asyncio.wait_for(task, 8)
            assert caught.value.status_code == 409
            assert not editor.in_transaction()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with schema_sessions() as verify:
        rows = (await verify.scalars(select(Transaction).where(Transaction.workspace_id == ids['workspace']))).all()
        assert all(row.description != 'Must roll back' and row.amount == Decimal('10') for row in rows)
        assert all(stamp(row) == ('needs_review', None, None, None, None) for row in rows)


async def test_dirty_target_rejected_before_autoflush_or_refresh(schema_sessions):
    ids, _ = await seed(schema_sessions, 'dirty')
    async with schema_sessions() as session:
        row = await session.get(Transaction, ids['x'])
        assert row is not None
        row.description = 'Unflushed must never land'
        statements = []
        connection = await session.connection()
        def record(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(connection.sync_connection, 'before_cursor_execute', record)
        with pytest.raises(HTTPException) as caught:
            await transaction_service.review_transaction(session, ids['x'], ids['workspace'], ids['a'], 'reviewed')
        assert caught.value.status_code == 409
        assert not session.in_transaction()
        assert not any(sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE', 'SELECT')) for sql in statements)
    async with schema_sessions() as verify:
        row = await verify.get(Transaction, ids['x'])
        assert row.description == 'writer-regression'
        assert stamp(row) == ('needs_review', None, None, None, None)
