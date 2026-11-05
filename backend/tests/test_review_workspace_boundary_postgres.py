"""Fresh generic PATCH write sets cannot escape their authorized workspace."""
import asyncio
import copy
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.transaction import TransactionUpdate
from app.services import transaction_service
from tests.test_review_concurrency_postgres import schema_sessions as schema_sessions, snapshot
from tests.test_review_writer_races_postgres import seed, stamp, wait_for_real_block

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('moved', ['anchor', 'series', 'counterpart', 'total'])
async def test_fresh_locked_write_set_remains_in_authorized_workspace(schema_sessions, moved):
    ids, _ = await seed(schema_sessions, 'workspace-boundary')
    destination = uuid.uuid4()
    async with schema_sessions() as setup:
        setup.add(Workspace(id=destination, name='Unauthorized B', created_by_user_id=ids['b']))
        setup.add(WorkspaceMember(workspace_id=ids['workspace'], user_id=ids['a'], role='editor'))
        anchor = await setup.get(Transaction, ids['x'])
        assert anchor is not None
        if moved != 'anchor':
            other = Transaction(id=ids['y'], user_id=ids['owner'], workspace_id=ids['workspace'],
                account_id=ids['account'], description='Sibling unchanged', amount=Decimal('10'),
                currency='USD', date=date(2026, 10, 15), effective_date=date(2026, 10, 15),
                type='debit', source='import', review_status='needs_review')
            setup.add(other)
            if moved == 'counterpart':
                anchor.transfer_pair_id = other.transfer_pair_id = uuid.uuid4()
            else:
                series = uuid.uuid4()
                for row, number in [(anchor, 1), (other, 2)]:
                    row.installment_series_id = series
                    row.installment_number = number
                    row.total_installments = 2
                    row.installment_total_amount = Decimal('20')
                    row.installment_purchase_date = date(2026, 10, 15)
        await setup.commit()
    before = await snapshot(schema_sessions)
    moved_id = ids['x'] if moved == 'anchor' else ids['y']
    async with schema_sessions() as mover, schema_sessions() as editor, schema_sessions() as observer:
        row = await mover.get(Transaction, moved_id)
        assert row is not None
        row.workspace_id = destination
        await mover.flush()  # Uncommitted move: scoped discovery still sees A.
        mover_pid = await mover.scalar(text('SELECT pg_backend_pid()'))
        editor_pid = await editor.scalar(text('SELECT pg_backend_pid()'))
        task = asyncio.create_task(transaction_service.update_transaction(
            editor, ids['x'], ids['workspace'], ids['a'], TransactionUpdate(
                description='Forbidden A patch', amount=Decimal('99'), review_status='reviewed',
                apply_to='all' if moved == 'series' else 'this')))
        try:
            await wait_for_real_block(observer, task, editor_pid, mover_pid)
            await mover.commit()
            with pytest.raises(HTTPException) as caught:
                await asyncio.wait_for(task, 8)
            assert caught.value.status_code == 409
            assert not editor.in_transaction()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    expected = copy.deepcopy(before)
    next(row for row in expected['transactions'] if row['id'] == moved_id)['workspace_id'] = destination
    # All physical tables, money, totals, other rows and five attribution fields.
    assert await snapshot(schema_sessions) == expected


async def test_authorized_workspace_patch_positive_control(schema_sessions):
    ids, _ = await seed(schema_sessions, 'workspace-control')
    async with schema_sessions() as session:
        result = await transaction_service.update_transaction(
            session, ids['x'], ids['workspace'], ids['a'],
            TransactionUpdate(description='Authorized patch', amount=Decimal('12'), review_status='reviewed'))
        assert result is not None
    async with schema_sessions() as verify:
        row = await verify.get(Transaction, ids['x'])
        assert row.workspace_id == ids['workspace']
        assert row.description == 'Authorized patch' and row.amount == Decimal('12')
        assert stamp(row)[0] == 'reviewed' and stamp(row)[2:] == (ids['a'], 'human', None)
