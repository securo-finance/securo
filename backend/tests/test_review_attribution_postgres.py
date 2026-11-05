"""Graph48 initial native candidate: real CHECK, V1 fence, deleted-rule retention.

NOT_RUN at authoring. Existing native fixtures and tests remain unchanged.
Both schema modes own fresh resources; Alembic runs the actual migration chain.
No URL means canonical reported skips, never fabricated native passes.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services import admin_service, rule_service, transaction_service
from app.services.review_attribution import HumanReviewPrincipal

from tests.test_review_concurrency_postgres import (
    schema_sessions as schema_sessions,
    seed,
    snapshot,
)

pytestmark = pytest.mark.asyncio
STAMP = datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc)
ORPHAN_RULE = uuid.UUID('a2222222-2222-4222-8222-222222222222')
CHECK = 'ck_transactions_review_attribution_shape'


def exact_check(exc, record_property):
    """Witness actual asyncpg driver diagnostics, not generic IntegrityError."""
    orig = exc.orig
    cause = orig.__cause__
    witness = {
        'orig_class': type(orig).__module__ + '.' + type(orig).__name__,
        'orig_sqlstate': getattr(orig, 'sqlstate', None),
        'cause_class': type(cause).__module__ + '.' + type(cause).__name__,
        'cause_sqlstate': getattr(cause, 'sqlstate', None),
        'constraint': getattr(cause, 'constraint_name', None),
    }
    record_property('native_check_driver', json.dumps(witness, sort_keys=True))
    assert witness['cause_class'] == 'asyncpg.exceptions.CheckViolationError'
    assert witness['orig_sqlstate'] == witness['cause_sqlstate'] == '23514'
    assert witness['constraint'] == CHECK


async def physical_row(factory, transaction_id):
    async with factory() as session:
        assert session.get_bind().dialect.name == 'postgresql'
        return dict((await session.execute(select(Transaction.__table__).where(
            Transaction.id == transaction_id))).mappings().one())


# Independent copies of the SQLite tracer tuples: do not import its test module.
@pytest.mark.parametrize('kind,status,stamp,has_user,rule_id', [
    pytest.param(None, 'reviewed', STAMP, False, ORPHAN_RULE, id='null_kind_with_rule_id'),
    pytest.param('robot', 'reviewed', STAMP, False, None, id='unknown_kind_robot'),
    pytest.param('human', 'reviewed', None, True, None, id='human_missing_time'),
    pytest.param('human', 'reviewed', STAMP, False, None, id='human_missing_user'),
    pytest.param('human', 'reviewed', STAMP, True, ORPHAN_RULE, id='human_with_rule_id'),
    pytest.param('human', 'needs_review', STAMP, True, None, id='human_needs_review'),
    pytest.param('rule', 'reviewed', STAMP, False, None, id='rule_missing_rule_id'),
    pytest.param('rule', 'reviewed', STAMP, True, ORPHAN_RULE, id='rule_with_user'),
    pytest.param('rule', 'reviewed', None, False, ORPHAN_RULE, id='rule_missing_time'),
    pytest.param('rule', 'needs_review', STAMP, False, ORPHAN_RULE, id='rule_needs_review'),
])
async def test_native_check_rejects_invalid_shape(
    schema_sessions, kind, status, stamp, has_user, rule_id, record_property,
):
    ids = await seed(schema_sessions)  # Plain UUIDs survive rollback expiration.
    before = await snapshot(schema_sessions)
    async with schema_sessions() as session:
        target = await session.get(Transaction, ids['tx'])
        primary = None
        try:
            target.review_status = status
            target.reviewed_at = stamp
            target.reviewed_by_id = ids['reviewer'] if has_user else None
            target.review_attribution_kind = kind
            target.review_rule_id = rule_id
            with pytest.raises(IntegrityError) as rejected:
                await session.flush()
            exact_check(rejected.value, record_property)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                await session.rollback()
                assert not session.in_transaction()
            except BaseException as secondary:
                if primary is None:
                    raise
                primary.add_note('Rollback also failed: ' + type(secondary).__name__)
    assert await snapshot(schema_sessions) == before


@pytest.mark.parametrize('kind,status,stamp,has_user,rule_id', [
    pytest.param(None, 'reviewed', STAMP, True, None, id='legacy_reviewed_paired'),
    pytest.param(None, 'reviewed', None, False, None, id='legacy_reviewed_both_null'),
    pytest.param(None, 'reviewed', STAMP, False, None, id='legacy_reviewed_time_only'),
    pytest.param(None, 'reviewed', None, True, None, id='legacy_reviewed_user_only'),
    pytest.param(None, 'needs_review', STAMP, True, None, id='legacy_needs_review_paired'),
    pytest.param(None, 'needs_review', None, False, None, id='legacy_needs_review_both_null'),
    pytest.param(None, 'needs_review', STAMP, False, None, id='legacy_needs_review_time_only'),
    pytest.param(None, 'needs_review', None, True, None, id='legacy_needs_review_user_only'),
    pytest.param('human', 'reviewed', STAMP, True, None, id='v2_complete_human'),
    pytest.param('rule', 'reviewed', STAMP, False, ORPHAN_RULE, id='v2_complete_rule_no_rule_row'),
])
async def test_native_check_accepts_legacy_and_complete_shapes(
    schema_sessions, kind, status, stamp, has_user, rule_id,
):
    ids = await seed(schema_sessions)
    before = await physical_row(schema_sessions, ids['tx'])
    shape = dict(review_status=status, reviewed_at=stamp,
                 reviewed_by_id=ids['reviewer'] if has_user else None,
                 review_attribution_kind=kind, review_rule_id=rule_id)
    async with schema_sessions() as session:
        connection = await session.connection()
        schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
        rule_fks = (await session.execute(text(
            "SELECT c.conname FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey) "
            "WHERE c.conrelid=to_regclass(:table) AND c.contype='f' "
            "AND a.attname='review_rule_id'"), {'table': schema + '.transactions'})).all()
        assert not rule_fks
        assert await session.get(Rule, ORPHAN_RULE) is None
        target = await session.get(Transaction, ids['tx'])
        for field, value in shape.items():
            setattr(target, field, value)
        await session.flush()
        await session.commit()
    after = await physical_row(schema_sessions, ids['tx'])
    assert after == {**before, **shape}
    if stamp is not None:
        assert after['reviewed_at'].utcoffset() == STAMP.utcoffset()


async def test_native_v1_unreview_fenced_then_v2_clear_and_rereview(
    schema_sessions, record_property,
):
    ids = await seed(schema_sessions)
    async with schema_sessions() as session:
        target = await session.get(Transaction, ids['tx'])
        assert await transaction_service.set_review_status(
            session, ids['workspace'], target, 'reviewed',
            HumanReviewPrincipal(ids['reviewer'])) == 1
        await session.commit()
    before = await snapshot(schema_sessions)
    first = await physical_row(schema_sessions, ids['tx'])
    assert first['review_attribution_kind'] == 'human'
    assert first['reviewed_by_id'] == ids['reviewer']
    assert first['review_rule_id'] is None
    assert first['reviewed_at'] is not None and first['reviewed_at'].utcoffset() is not None
    async with schema_sessions() as old_writer:
        connection = await old_writer.connection()
        schema = connection.sync_connection.get_execution_options()['schema_translate_map'][None]
        primary = None
        try:
            with pytest.raises(IntegrityError) as rejected:
                # Exactly V1 columns; no helper or new-field clearing.
                await old_writer.execute(text(
                    'UPDATE "' + schema + '".transactions SET review_status=:status, '
                    'reviewed_at=NULL, reviewed_by_id=NULL WHERE id=:id'),
                    {'status': 'needs_review', 'id': ids['tx']})
            exact_check(rejected.value, record_property)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                await old_writer.rollback()
                assert not old_writer.in_transaction()
            except BaseException as secondary:
                if primary is None:
                    raise
                primary.add_note('Rollback also failed: ' + type(secondary).__name__)
    assert await snapshot(schema_sessions) == before
    async with schema_sessions() as session:
        target = await session.get(Transaction, ids['tx'])
        assert await transaction_service.set_review_status(
            session, ids['workspace'], target, 'needs_review', None) == 1
        await session.commit()
    cleared = await physical_row(schema_sessions, ids['tx'])
    assert cleared == {**first, 'review_status': 'needs_review', 'reviewed_at': None,
                       'reviewed_by_id': None, 'review_attribution_kind': None,
                       'review_rule_id': None}
    async with schema_sessions() as session:
        target = await session.get(Transaction, ids['tx'])
        assert await transaction_service.set_review_status(
            session, ids['workspace'], target, 'reviewed',
            HumanReviewPrincipal(ids['reviewer'])) == 1
        stamp = target.reviewed_at
        await session.commit()
    assert stamp is not None and stamp.utcoffset() is not None
    assert await physical_row(schema_sessions, ids['tx']) == {
        **cleared, 'review_status': 'reviewed', 'reviewed_at': stamp,
        'reviewed_by_id': ids['reviewer'], 'review_attribution_kind': 'human'}


async def test_native_deleted_rule_and_rule_owner_preserve_survivor(schema_sessions):
    ids = await seed(schema_sessions)
    async with schema_sessions() as session:
        target = await session.get(Transaction, ids['tx'])
        assert target.user_id == ids['owner'] != ids['reviewer']
        rule = await rule_service.create_rule(
            session, ids['workspace'], ids['reviewer'],
            RuleCreate(name='Synthetic native retained rule UUID',
                       conditions=[RuleCondition(field='description', op='equals',
                                                 value=target.description)],
                       actions=[RuleAction(op='set_reviewed', value=None)], is_active=True))
        rule_id = rule.id
        assert rule.user_id == ids['reviewer'] and rule.workspace_id == ids['workspace']
        assert await rule_service.apply_single_rule(session, ids['workspace'], rule) == 1
    stamped = await physical_row(schema_sessions, ids['tx'])
    assert stamped['review_status'] == 'reviewed' and stamped['reviewed_by_id'] is None
    assert stamped['review_attribution_kind'] == 'rule' and stamped['review_rule_id'] == rule_id
    assert stamped['reviewed_at'] is not None and stamped['reviewed_at'].utcoffset() is not None
    async with schema_sessions() as session:
        rule = await session.get(Rule, rule_id)
        assert rule is not None
        await session.delete(rule)
        await session.commit()
    assert await physical_row(schema_sessions, ids['tx']) == stamped
    async with schema_sessions() as session:
        assert await session.get(Rule, rule_id) is None
        assert await admin_service.delete_user(session, ids['reviewer'], ids['admin']) is True
    state = await snapshot(schema_sessions)
    assert ids['reviewer'] not in {row['id'] for row in state['users']}
    assert rule_id not in {row['id'] for row in state['rules']}
    assert next(row for row in state['transactions'] if row['id'] == ids['tx']) == stamped
