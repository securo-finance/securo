"""Native writer races: actual blocking, stale identity maps and provenance."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models.account import Account
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.services import rule_service, transaction_service

from tests.test_review_concurrency_postgres import schema_sessions as schema_sessions

pytestmark = pytest.mark.asyncio
FIELDS = ('review_status', 'reviewed_at', 'reviewed_by_id', 'review_attribution_kind', 'review_rule_id')


def stamp(tx):
    return tuple(getattr(tx, field) for field in FIELDS)


async def seed(factory, scenario):
    ids = {key: uuid.uuid4() for key in ('owner', 'a', 'b', 'workspace', 'account', 'rule', 'x', 'y', 'z')}
    ordered = sorted(ids[key] for key in ('x', 'y', 'z'))
    ids.update(z=ordered[0], x=ordered[1], y=ordered[2])
    async with factory() as session:
        session.add_all([User(id=ids[key], email=f'{key}-{ids[key]}@writer.invalid',
                              hashed_password='synthetic-unused', is_active=True,
                              is_verified=True) for key in ('owner', 'a', 'b')])
        await session.flush()
        session.add(Workspace(id=ids['workspace'], name='Synthetic writer regression',
                              created_by_user_id=ids['owner']))
        await session.flush()
        session.add(Account(id=ids['account'], user_id=ids['owner'], workspace_id=ids['workspace'],
                            name='Synthetic', type='checking', currency='USD', balance=Decimal('0')))
        await session.flush()
        # x is the single conflict row; y/z are bulk-only non-overlap controls.
        keys = ('x', 'y', 'z') if scenario == 'overlapping-bulks' else ('x',)
        for key in keys:
            session.add(Transaction(id=ids[key], user_id=ids['owner'], workspace_id=ids['workspace'],
                                    account_id=ids['account'], description='writer-regression',
                                    amount=Decimal('10'), currency='USD', date=date(2026, 10, 15),
                                    effective_date=date(2026, 10, 15), type='debit', source='import',
                                    review_status='needs_review'))
        session.add(Rule(id=ids['rule'], user_id=ids['owner'], workspace_id=ids['workspace'],
                         name='Synthetic review only', conditions_op='and',
                         conditions=[{'field': 'description', 'op': 'equals', 'value': 'writer-regression'}],
                         actions=[{'op': 'set_reviewed', 'value': None}], priority=0, is_active=True))
        await session.commit()
    return ids, keys


async def invoke(session, ids, operation, actor):
    if operation == 'patch':
        from app.schemas.transaction import TransactionUpdate
        return await transaction_service.update_transaction(
            session, ids['x'], ids['workspace'], ids[actor], TransactionUpdate(review_status='reviewed'),
        )
    if operation == 'existing-rule-helper':
        tx = await session.get(Transaction, ids['x'])
        await rule_service.apply_rules_to_transaction(session, ids['owner'], tx)
        await session.commit()
        return tx
    if operation == 'single':
        return await transaction_service.review_transaction(session, ids['x'], ids['workspace'], ids[actor], 'reviewed')
    if operation in ('bulk-one', 'bulk-a', 'bulk-b'):
        keys = ('x',) if operation == 'bulk-one' else ('y', 'x') if operation == 'bulk-a' else ('x', 'z')
        return await transaction_service.bulk_set_review(session, ids['workspace'], ids[actor],
                                                         [ids[key] for key in keys], 'reviewed')
    if operation == 'single-rule':
        rule = await session.get(Rule, ids['rule'])
        return await rule_service.apply_single_rule(session, ids['workspace'], rule)
    if operation == 'all-rules':
        return await rule_service.apply_all_rules(session, ids['workspace'])
    raise AssertionError(operation)


async def wait_for_real_block(observer, task, waiter_pid, blocker_pid):
    # Both the broken UPDATE and fixed SELECT/CAS block behind A's flushed
    # row. Never wait for B's SELECT result, which a correct lock prevents.
    async with asyncio.timeout(8):
        while True:
            if task.done():
                await task  # propagate the real exception, not a timeout label
                raise AssertionError('Contending writer finished while A held the uncommitted row')
            row = (await observer.execute(text(
                'SELECT wait_event_type, query AS waiting_query, :blocker = ANY(pg_blocking_pids(pid)) AS blocked '
                'FROM pg_stat_activity WHERE pid = :waiter'),
                {'blocker': blocker_pid, 'waiter': waiter_pid})).one()
            await observer.rollback()  # fresh pg_stat_activity snapshot next poll
            if row.wait_event_type == 'Lock' and row.blocked:
                assert row.waiting_query.lstrip().upper().startswith('SELECT'), row.waiting_query
                print('LOCKED_READ_WITNESS', {'waiter': waiter_pid, 'blocker': blocker_pid,
                                            'wait_event_type': row.wait_event_type, 'blocked': row.blocked})
                return
            # SQL IO yields; no time-based correctness assumption or sleep.


SCENARIOS = [
    ('two-humans-single', 'single', 'single'),
    ('two-humans-counted', 'bulk-one', 'bulk-one'),
    ('single-v-bulk', 'single', 'bulk-one'),
    ('bulk-v-single', 'bulk-one', 'single'),
    ('human-v-single-rule', 'single', 'single-rule'),
    ('single-rule-v-human', 'single-rule', 'bulk-one'),
    ('human-v-all-rules', 'single', 'all-rules'),
    ('all-rules-v-human', 'all-rules', 'bulk-one'),
    ('overlapping-bulks', 'bulk-a', 'bulk-b'),
    ('human-v-patch', 'single', 'patch'),
    ('patch-v-human', 'patch', 'bulk-one'),
    ('patch-v-rule', 'patch', 'single-rule'),
    ('rule-v-patch', 'single-rule', 'patch'),
    ('human-v-existing-rule-helper', 'single', 'existing-rule-helper'),
    ('existing-rule-helper-v-human', 'existing-rule-helper', 'bulk-one'),
]


@pytest.mark.parametrize('preload_loser', [False, True], ids=['fresh-session', 'held-identity-map'])
@pytest.mark.parametrize('scenario,winner_op,loser_op', SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_first_committer_preserves_stamp_and_counts(schema_sessions, monkeypatch,
                                                         scenario, winner_op, loser_op, preload_loser):
    postgres_sessions = schema_sessions
    ids, keys = await seed(postgres_sessions, scenario)
    async with postgres_sessions() as winner, postgres_sessions() as loser, postgres_sessions() as observer:
        # Retain strong refs: SQLAlchemy's identity map normally weak-references
        # clean objects. This catches a lock-only fix that forgets to refresh.
        held_rows = list((await loser.execute(select(Transaction).where(
            Transaction.workspace_id == ids['workspace']))).scalars()) if preload_loser else []
        a_pid = await winner.scalar(text('SELECT pg_backend_pid()'))
        b_pid = await loser.scalar(text('SELECT pg_backend_pid()'))
        ready, release = asyncio.Event(), asyncio.Event()
        captured = {}
        real_commit = winner.commit

        async def hold_commit():
            # Test-only scheduling hook at the actual production commit owner;
            # flush performs real PostgreSQL writes and retains native locks.
            await winner.flush()
            rows = (await winner.execute(select(Transaction).where(
                Transaction.workspace_id == ids['workspace']))).scalars().all()
            captured.update({row.id: stamp(row) for row in rows})
            ready.set()
            await release.wait()
            await real_commit()

        monkeypatch.setattr(winner, 'commit', hold_commit)
        a = asyncio.create_task(invoke(winner, ids, winner_op, 'a'))
        b = None
        ready_task = None
        try:
            async with asyncio.timeout(8):
                # An early A exception is surfaced instead of silently hanging.
                ready_task = asyncio.create_task(ready.wait())
                done, _ = await asyncio.wait([a, ready_task], return_when=asyncio.FIRST_COMPLETED)
                if a in done and not ready.is_set():
                    ready_task.cancel()
                    await asyncio.gather(ready_task, return_exceptions=True)
                    await a
                    raise AssertionError('Winner failed to reach a persistence boundary')
                await ready_task
            b = asyncio.create_task(invoke(loser, ids, loser_op, 'b'))
            await wait_for_real_block(observer, b, b_pid, a_pid)
            release.set()  # release on a real lock witness, not a timer or B-read barrier
            a_result, b_result = await asyncio.wait_for(asyncio.gather(a, b), 8)
        finally:
            release.set()
            for task in (a, b, ready_task):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            # Roll back only these owned sessions, never kill external processes.
            await winner.rollback()
            await loser.rollback()
        assert held_rows or not preload_loser

    async with postgres_sessions() as verify:
        final = {row.id: stamp(row) for row in (await verify.execute(select(Transaction).where(
            Transaction.workspace_id == ids['workspace']))).scalars()}
        assert final[ids['x']] == captured[ids['x']], 'Loser replaced winning attribution/time'
        winner_is_rule = winner_op in ('single-rule', 'all-rules', 'existing-rule-helper')
        expected_principal = (None, 'rule', ids['rule']) if winner_is_rule else (ids['a'], 'human', None)
        assert final[ids['x']][0] == 'reviewed' and final[ids['x']][1] is not None
        assert final[ids['x']][2:] == expected_principal
        if scenario == 'overlapping-bulks':
            assert a_result == {'updated': 2, 'matched': 2}
            assert b_result == {'updated': 1, 'matched': 2}
            assert final[ids['y']] == captured[ids['y']]
            assert final[ids['z']][2:] == (ids['b'], 'human', None)
        else:
            if winner_op == 'bulk-one':
                assert a_result == {'updated': 1, 'matched': 1}
            if loser_op == 'bulk-one':
                assert b_result == {'updated': 0, 'matched': 1}
            if loser_op == 'single-rule':
                assert b_result == 0  # rule has no actions except set_reviewed
            if loser_op == 'all-rules':
                assert b_result == 1  # existing API counts matched, NOT transitions
        # Re-run through both counted public service paths: no-restamp and 0
        # actual updates. Singles have no public transition-count response.
        before = dict(final)
        assert await transaction_service.bulk_set_review(verify, ids['workspace'], ids['b'],
                                                        [ids[key] for key in keys], 'reviewed') == {
                                                            'updated': 0, 'matched': len(keys)}
        assert await rule_service.apply_single_rule(verify, ids['workspace'],
                                                    await verify.get(Rule, ids['rule'])) == 0
        assert await rule_service.apply_all_rules(verify, ids['workspace']) == len(keys)
    async with postgres_sessions() as verify:
        after = {row.id: stamp(row) for row in (await verify.execute(select(Transaction).where(
            Transaction.workspace_id == ids['workspace']))).scalars()}
        assert after == before
