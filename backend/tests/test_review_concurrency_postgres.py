"""Real PostgreSQL adapter, FK-arbitrated races and legitimate deletion control."""
import asyncio
import copy
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.services import admin_service, transaction_service
from app.services.review_conflict import (
    ReviewerAttributionRace,
    ReviewerDeleteRace,
    is_reviewer_fk_violation,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture(params=['metadata', 'alembic'])
def schema_sessions(request):
    # Resolve only the selected independently owned native fixture. Keep this
    # selector synchronous so pytest-asyncio owns async fixture setup/teardown.
    fixture = 'postgres_sessions' if request.param == 'metadata' else 'alembic_postgres_sessions'
    return request.getfixturevalue(fixture)


async def seed(factory):
    ids = {key: uuid.uuid4() for key in ('owner', 'reviewer', 'admin', 'workspace', 'account', 'tx')}
    async with factory() as session:
        session.add_all([User(id=ids[key], email=f'{key}@concurrency.example',
                             hashed_password='synthetic-unused-no-login', is_active=True,
                             is_verified=True, is_superuser=key == 'admin')
                         for key in ('owner', 'reviewer', 'admin')])
        await session.flush()
        session.add(Workspace(id=ids['workspace'], name='Synthetic concurrency', created_by_user_id=ids['owner']))
        await session.flush()
        session.add(Account(id=ids['account'], user_id=ids['owner'], workspace_id=ids['workspace'],
                            name='Synthetic account', type='checking', currency='USD', balance=Decimal('10')))
        await session.flush()
        session.add(Transaction(id=ids['tx'], user_id=ids['owner'], workspace_id=ids['workspace'],
                                account_id=ids['account'], description='Synthetic concurrency transaction',
                                amount=Decimal('10'), currency='USD', date=date(2026, 10, 15),
                                effective_date=date(2026, 10, 15), type='debit', source='import',
                                review_status='needs_review'))
        await session.commit()
    return ids


async def test_current_and_alembic_schema_fk(schema_sessions):
    async with schema_sessions() as session:
        connection = await session.connection()
        schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
        row = (await session.execute(text(
            'SELECT confdeltype::text AS confdeltype, condeferrable, condeferred FROM pg_constraint '
            'WHERE conname = :name AND conrelid = to_regclass(:table)'),
            {'name': 'fk_transactions_reviewed_by_id', 'table': schema + '.transactions'})).one()
        assert row.confdeltype == 'r'
        assert row.condeferrable is False and row.condeferred is False


async def snapshot(factory):
    async with factory() as session:
        return {table.name: [dict(row) for row in (await session.execute(
            select(table).order_by(*table.primary_key.columns))).mappings()]
                for table in Base.metadata.sorted_tables}


async def test_native_adapter_qualification(postgres_sessions):
    """Intentionally violate the actual named FK; no driver mocks."""
    async with postgres_sessions() as session:
        await session.execute(text('CREATE TEMP TABLE adapter_users (id integer PRIMARY KEY)'))
        await session.execute(text(
            'CREATE TEMP TABLE adapter_reviews (reviewer_id integer CONSTRAINT '
            'fk_transactions_reviewed_by_id REFERENCES adapter_users(id))'))
        try:
            await session.execute(text('INSERT INTO adapter_reviews VALUES (42)'))
        except IntegrityError as exc:
            orig = exc.orig
            assert orig is not None
            cause = orig.__cause__
            shape = {'sqlalchemy': type(exc).__module__ + '.' + type(exc).__name__,
                     'orig': type(orig).__module__ + '.' + type(orig).__name__,
                     'orig_sqlstate': getattr(orig, 'sqlstate', None),
                     'orig_constraint_present': hasattr(orig, 'constraint_name'),
                     'direct_cause': type(cause).__module__ + '.' + type(cause).__name__,
                     'direct_constraint': getattr(cause, 'constraint_name', None)}
            print('NATIVE_ADAPTER_SHAPE', json.dumps(shape, sort_keys=True))
            assert is_reviewer_fk_violation(exc) is True
            await session.rollback()
            assert not session.in_transaction()
        else:
            pytest.fail('Real PostgreSQL FK did not reject absent synthetic reviewer')


async def wait_for_fk_wait(observer, waiter, blocker):
    async with asyncio.timeout(5):
        while True:
            row = (await observer.execute(text(
                'SELECT wait_event_type, :blocker = ANY(pg_blocking_pids(pid)) AS blocked '
                'FROM pg_stat_activity WHERE pid = :waiter'),
                {'blocker': blocker, 'waiter': waiter})).one()
            if row.wait_event_type == 'Lock' and row.blocked:
                return
            await observer.rollback()
            await asyncio.sleep(0.01)  # Poll pacing only; real lock state is the witness.


async def test_f1_review_wins_delete_rolls_back(schema_sessions):
    postgres_sessions = schema_sessions
    ids = await seed(postgres_sessions)
    before = await snapshot(postgres_sessions)
    async with postgres_sessions() as writer, postgres_sessions() as deleter, postgres_sessions() as observer:
        bpid = (await writer.execute(text('SELECT pg_backend_pid()'))).scalar_one()
        apid = (await deleter.execute(text('SELECT pg_backend_pid()'))).scalar_one()
        tx = await writer.get(Transaction, ids['tx'])
        await transaction_service.set_review_status(writer, ids['workspace'], tx, 'reviewed', ids['reviewer'])
        await writer.flush()  # Retains real FK key-share until B commits.
        stamp = (tx.review_status, tx.reviewed_by_id, tx.reviewed_at,
                 tx.review_attribution_kind, tx.review_rule_id)
        task = asyncio.create_task(admin_service.delete_user(deleter, ids['reviewer'], ids['admin']))
        try:
            await wait_for_fk_wait(observer, apid, bpid)
            assert not task.done(), 'Delete must genuinely wait behind uncommitted review'
            await writer.commit()
            with pytest.raises(ReviewerDeleteRace) as caught:
                await asyncio.wait_for(task, 5)
            assert str(caught.value) == "Cannot delete a user who reviewed another user's transactions"
            assert not deleter.in_transaction(), 'Delete commit owner must roll back'
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    expected = copy.deepcopy(before)
    row = next(row for row in expected['transactions'] if row['id'] == ids['tx'])
    row.update(zip(('review_status', 'reviewed_by_id', 'reviewed_at',
                    'review_attribution_kind', 'review_rule_id'), stamp, strict=True))
    assert await snapshot(postgres_sessions) == expected


async def test_f2_delete_wins_attribution_rolls_back(schema_sessions):
    postgres_sessions = schema_sessions
    ids = await seed(postgres_sessions)
    async with postgres_sessions() as late, postgres_sessions() as deleter:
        # Load before deletion, then execute the real owner with the captured actor.
        await late.get(Transaction, ids['tx'])
        assert await admin_service.delete_user(deleter, ids['reviewer'], ids['admin']) is True
        with pytest.raises(ReviewerAttributionRace) as caught:
            await transaction_service.review_transaction(late, ids['tx'], ids['workspace'], ids['reviewer'], 'reviewed')
        assert str(caught.value) == 'Review could not be saved because the reviewer account was deleted.'
        assert not late.in_transaction(), 'Attribution owner must roll back'
    state = await snapshot(postgres_sessions)
    assert ids['reviewer'] not in {row['id'] for row in state['users']}
    row = next(row for row in state['transactions'] if row['id'] == ids['tx'])
    assert row['review_status'] == 'needs_review'
    assert row['reviewed_by_id'] is None and row['reviewed_at'] is None


async def test_legitimate_target_owned_review_deletion(schema_sessions):
    postgres_sessions = schema_sessions
    ids = await seed(postgres_sessions)
    async with postgres_sessions() as session:
        tx = await session.get(Transaction, ids['tx'])
        await transaction_service.set_review_status(session, ids['workspace'], tx, 'reviewed', ids['owner'])
        await session.commit()
        assert await admin_service.delete_user(session, ids['owner'], ids['admin']) is True
    state = await snapshot(postgres_sessions)
    assert not state['transactions'] and not state['accounts']
    assert ids['owner'] not in {row['id'] for row in state['users']}


async def test_native_owner_query_autoflush_rollback(schema_sessions):
    ids = await seed(schema_sessions)
    async with schema_sessions() as late, schema_sessions() as deleter:
        tx = await late.get(Transaction, ids['tx'])
        await transaction_service.set_review_status(late, ids['workspace'], tx, 'reviewed', ids['reviewer'])
        assert await admin_service.delete_user(deleter, ids['reviewer'], ids['admin']) is True
        # A creation owner's first SELECT still exercises the committing
        # decorator's genuine FK/autoflush rollback. Existing-row review now
        # rejects dirty targets before any flush (covered separately).
        from app.schemas.transaction import TransactionCreate

        with pytest.raises(ReviewerAttributionRace) as caught:
            await transaction_service.create_transaction(
                late, ids['workspace'], ids['owner'],
                TransactionCreate(account_id=ids['account'], description='Autoflush owner control',
                                  amount=Decimal('1'), currency='USD', date=date(2026, 10, 15), type='debit'),
            )
        assert isinstance(caught.value.__cause__, IntegrityError)
        assert 'autoflush' in str(caught.value.__cause__)
        assert not late.in_transaction()
    state = await snapshot(schema_sessions)
    row = next(row for row in state['transactions'] if row['id'] == ids['tx'])
    assert row['review_status'] == 'needs_review'
    assert row['reviewed_by_id'] is None and row['reviewed_at'] is None


@pytest.mark.parametrize('winner', ['review', 'delete'], ids=['F1', 'F2'])
async def test_native_authenticated_http_race(schema_sessions, winner):
    from httpx import ASGITransport, AsyncClient
    from app.core.auth import get_jwt_strategy
    from app.core.database import get_async_session
    from app.main import app
    from app.models.workspace import WorkspaceMember

    ids = await seed(schema_sessions)
    async with schema_sessions() as setup:
        setup.add(WorkspaceMember(workspace_id=ids['workspace'], user_id=ids['reviewer'], role='editor'))
        await setup.commit()
        actor = await setup.get(User, ids['admin' if winner == 'review' else 'reviewer'])
        token = await get_jwt_strategy().write_token(actor)
    before = await snapshot(schema_sessions)
    ready = asyncio.Event()
    witness = {}

    async def native_session():
        async with schema_sessions() as session:
            witness['pid'] = (await session.execute(text('SELECT pg_backend_pid()'))).scalar_one()
            ready.set()
            try:
                yield session
            finally:
                witness['rolled_back'] = not session.in_transaction()

    previous = app.dependency_overrides.get(get_async_session)
    app.dependency_overrides[get_async_session] = native_session
    try:
        async with schema_sessions() as holder, schema_sessions() as observer:
            connection = await holder.connection()
            schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
            blocker = (await holder.execute(text('SELECT pg_backend_pid()'))).scalar_one()
            if winner == 'review':
                tx = await holder.get(Transaction, ids['tx'])
                await transaction_service.set_review_status(holder, ids['workspace'], tx, 'reviewed', ids['reviewer'])
                await holder.flush()
                stamp = (tx.review_status, tx.reviewed_by_id, tx.reviewed_at,
                         tx.review_attribution_kind, tx.review_rule_id)
            else:
                # Same real deletion session owns the table lock; authentication
                # and membership SELECTs proceed, then request transaction SELECT blocks.
                await holder.execute(text('LOCK TABLE "' + schema + '".transactions IN ACCESS EXCLUSIVE MODE'))
            headers = {'Authorization': 'Bearer ' + token, 'X-Workspace-Id': str(ids['workspace'])}
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                if winner == 'review':
                    task = asyncio.create_task(client.delete('/api/admin/users/' + str(ids['reviewer']), headers=headers))
                else:
                    task = asyncio.create_task(client.patch('/api/transactions/' + str(ids['tx']) + '/review', headers=headers, json={'review_status': 'reviewed'}))
                try:
                    await asyncio.wait_for(ready.wait(), 5)
                    await wait_for_fk_wait(observer, witness['pid'], blocker)
                    assert not task.done()
                    if winner == 'delete':
                        assert await admin_service.delete_user(holder, ids['reviewer'], ids['admin']) is True
                    else:
                        await holder.commit()
                    response = await asyncio.wait_for(task, 5)
                    expected_conflict = ReviewerDeleteRace() if winner == 'review' else ReviewerAttributionRace()
                    assert response.status_code == 409, response.text
                    assert response.json() == {'detail': str(expected_conflict)}
                    assert witness['rolled_back']
                    print('NATIVE_HTTP_LOCK_WITNESS', winner, schema, witness['pid'], blocker, response.status_code)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_async_session, None)
        else:
            app.dependency_overrides[get_async_session] = previous
    expected = copy.deepcopy(before)
    if winner == 'review':
        row = next(row for row in expected['transactions'] if row['id'] == ids['tx'])
        row.update(zip(('review_status', 'reviewed_by_id', 'reviewed_at',
                        'review_attribution_kind', 'review_rule_id'), stamp, strict=True))
    else:
        expected['users'] = [row for row in expected['users'] if row['id'] != ids['reviewer']]
        expected['workspace_members'] = [row for row in expected['workspace_members'] if row['user_id'] != ids['reviewer']]
    assert await snapshot(schema_sessions) == expected
