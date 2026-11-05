"""Persistence boundary checks use real owners and exact adapter-shaped errors."""
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asyncpg.exceptions import ForeignKeyViolationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError

from app.schemas.transaction import TransactionCreate
from app.services import admin_service, connection_service, import_service, rule_service, transaction_service
from app.services.review_conflict import ReviewerAttributionRace, ReviewerDeleteRace


def integrity(state='23503', constraint='fk_transactions_reviewed_by_id'):
    cause = ForeignKeyViolationError('synthetic FK probe')
    setattr(cause, 'constraint_name', constraint)
    orig = AsyncAdapt_asyncpg_dbapi.IntegrityError('synthetic adapter probe')
    setattr(orig, 'sqlstate', state)
    orig.__cause__ = cause
    return IntegrityError('synthetic', {}, orig)


OWNERS = [
    (admin_service, 'delete_user', ReviewerDeleteRace),
    *[(transaction_service, name, ReviewerAttributionRace) for name in (
        'review_transaction', 'create_transaction', 'create_installment_series',
        'update_transaction', 'bulk_set_review')],
    (rule_service, 'apply_single_rule', ReviewerAttributionRace),
    (rule_service, 'apply_all_rules', ReviewerAttributionRace),
    (import_service, 'import_transactions', ReviewerAttributionRace),
    (connection_service, 'handle_oauth_callback', ReviewerAttributionRace),
    (connection_service, 'sync_connection', ReviewerAttributionRace),
]


@pytest.mark.parametrize('module,name,conflict', OWNERS, ids=[item[1] for item in OWNERS])
def test_every_commit_owner_installs_full_boundary(module, name, conflict):
    owner = getattr(module, name)
    assert hasattr(owner, '__wrapped__'), 'Actual commit owner has no installed boundary'
    assert inspect.signature(owner) == inspect.signature(owner.__wrapped__)
    assert owner.__review_conflict__ is conflict


@pytest.mark.asyncio
@pytest.mark.parametrize('state,constraint,matched', [
    ('23503', 'fk_transactions_reviewed_by_id', True),
    ('23503', 'fk_other', False),
    ('23505', 'fk_transactions_reviewed_by_id', False),
])
async def test_actual_manual_create_intermediate_flush(
    session, test_user, test_workspace, test_account, monkeypatch, state, constraint, matched,
):
    exc = integrity(state, constraint)
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, 'rollback', rollback)
    flush = AsyncMock(side_effect=exc)
    monkeypatch.setattr(session, 'flush', flush)
    commit = AsyncMock()
    monkeypatch.setattr(session, 'commit', commit)
    data = TransactionCreate(account_id=test_account.id, description='Synthetic flush',
                             amount=10, date='2026-10-15', type='debit')
    with pytest.raises(ReviewerAttributionRace if matched else IntegrityError) as caught:
        await transaction_service.create_transaction(session, test_workspace.id, test_user.id, data)
    flush.assert_awaited_once()
    commit.assert_not_awaited()
    if matched:
        rollback.assert_awaited_once()
        assert caught.value.__cause__ is exc
    else:
        assert caught.value is exc
        rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('api_name,endpoint,service_name,owner', [
    ('admin', 'delete_user', 'admin_service', 'delete_user'),
    ('transactions', 'set_transaction_review', 'transaction_service', 'review_transaction'),
    ('transactions', 'bulk_review', 'transaction_service', 'bulk_set_review'),
    ('transactions', 'create_transaction', 'transaction_service', 'create_transaction'),
    ('transactions', 'create_installment_series', 'transaction_service', 'create_installment_series'),
    ('transactions', 'update_transaction', 'transaction_service', 'update_transaction'),
    ('rules', 'apply_all_rules', 'rule_service', 'apply_all_rules'),
    ('import_transactions', 'import_transactions', 'import_service', 'import_transactions'),
    ('connections', 'sync_connection', 'connection_service', 'sync_connection'),
    ('connections', 'oauth_callback', 'connection_service', 'handle_oauth_callback'),
])
async def test_http_module_explicit_conflict_mapping(monkeypatch, api_name, endpoint, service_name, owner):
    """Direct route-unit observation, not native authentication/race proof."""
    from importlib import import_module
    from fastapi import HTTPException

    module = import_module('app.api.' + api_name)
    conflict = ReviewerDeleteRace() if api_name == 'admin' else ReviewerAttributionRace()
    mocked = AsyncMock(side_effect=conflict)
    monkeypatch.setattr(getattr(module, service_name), owner, mocked)
    if api_name == 'import_transactions':
        monkeypatch.setattr(module.account_service, 'get_account', AsyncMock(return_value=object()))
    actor = uuid.uuid4()
    ctx = SimpleNamespace(workspace=SimpleNamespace(id=uuid.uuid4()), user_id=actor,
                          user=SimpleNamespace(primary_currency='USD'))
    data = SimpleNamespace(review_status='reviewed', transaction_ids=[uuid.uuid4()],
                           account_id=uuid.uuid4(), transactions=[], filename='',
                           detected_format='', detect_duplicates=True, code='synthetic',
                           provider=None, state=None, sync_assets=False, reconnect_connection_id=None)
    function = getattr(module, endpoint)
    values = {'session': object(), 'ctx': ctx, 'data': data,
              'current_user': SimpleNamespace(id=actor), 'user_id': uuid.uuid4(),
              'transaction_id': uuid.uuid4(), 'connection_id': uuid.uuid4()}
    arguments = {name: values[name] for name in inspect.signature(function).parameters if name in values}
    with pytest.raises(HTTPException) as caught:
        await function(**arguments)
    assert caught.value.status_code == 409
    assert caught.value.detail == str(conflict)
    mocked.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('matched', [True, False], ids=['matched', 'unrelated'])
async def test_actual_review_owner_execute_synthetic_adapter(session, test_user, test_workspace, monkeypatch, matched):
    exc = integrity(constraint='fk_transactions_reviewed_by_id' if matched else 'fk_other')
    execute = AsyncMock(side_effect=exc)
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, 'execute', execute)
    monkeypatch.setattr(session, 'rollback', rollback)
    with pytest.raises(ReviewerAttributionRace if matched else IntegrityError) as caught:
        await transaction_service.review_transaction(session, uuid.uuid4(), test_workspace.id, test_user.id, 'reviewed')
    execute.assert_awaited_once()
    if matched:
        rollback.assert_awaited_once()
        assert caught.value.__cause__ is exc
    else:
        rollback.assert_not_awaited()
        assert caught.value is exc


@pytest.mark.parametrize('matched', [True, False], ids=['matched', 'unrelated'])
def test_actual_background_task_failure_logging_synthetic_adapter(monkeypatch, caplog, matched):
    from app.tasks import sync_tasks
    exc = integrity(constraint='fk_transactions_reviewed_by_id' if matched else 'fk_other')
    session = AsyncMock()
    session.__aenter__.return_value = session
    # Connection workspace, fresh application timezone, then workspace timezone.
    session.scalar.side_effect = [uuid.uuid4(), 'UTC', 'UTC']
    session.execute.side_effect = exc
    engine = AsyncMock()
    monkeypatch.setattr(sync_tasks, '_make_session_maker', lambda: (engine, lambda: session))
    result = sync_tasks.sync_single_connection(str(uuid.uuid4()), str(uuid.uuid4()))
    assert result['status'] == 'error'
    assert session.scalar.await_count == 3
    session.execute.assert_awaited_once()
    record = next(r for r in caplog.records if 'Sync task failed for connection' in r.message)
    assert record.exc_info is not None
    caught = record.exc_info[1]
    if matched:
        assert isinstance(caught, ReviewerAttributionRace)
        assert caught.__cause__ is exc
        session.rollback.assert_awaited_once()
    else:
        assert caught is exc
        session.rollback.assert_not_awaited()
    engine.dispose.assert_awaited_once()
