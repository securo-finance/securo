"""Behavioral regressions for the two qualified review-field defects."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate, RuleUpdate
from app.services import rule_service, transaction_service
from app.services.rule_engine import evaluate_conditions


@pytest.mark.parametrize('review_status', ['needs_review', 'reviewed'])
async def test_review_filter_suppresses_running_balances(session, test_user, test_workspace, test_account, review_status):
    test_account.type = 'checking'
    test_account.currency = 'USD'
    for index, status in enumerate(('reviewed', 'needs_review')):
        session.add(Transaction(id=uuid.uuid4(), user_id=test_user.id,
                                workspace_id=test_workspace.id, account_id=test_account.id,
                                description=f'Synthetic balance {index}', amount=Decimal('10'),
                                currency='USD', date=date(2026, 10, 15 + index),
                                effective_date=date(2026, 10, 15 + index),
                                type='credit', source='import', review_status=status))
    await session.commit()
    rows, _, _ = await transaction_service.get_transactions(
        session, test_workspace.id, test_user.id, account_id=test_account.id,
        from_date=date(2026, 10, 1), include_opening_balance=True,
        include_running_balance=True, primary_currency='USD', sort_by='date', sort_dir='desc',
        review_status=review_status)
    assert rows
    assert all(getattr(row, 'running_balance', None) is None for row in rows)
    assert all(getattr(row, 'running_balance_primary', None) is None for row in rows)


@pytest.mark.parametrize('value', ['reviewed', 'REVIEWED', 'Reviewed', ' reviewed ', ' REVIEWED '])
async def test_review_literal_roundtrip(session, test_user, test_workspace, test_account, value):
    condition = RuleCondition(field='review_status', op='equals', value=value)
    rule = await rule_service.create_rule(session, test_workspace.id, test_user.id,
        RuleCreate(name='Review literal', conditions=[condition],
                   actions=[RuleAction(op='set_reviewed', value=None)]))
    assert rule.conditions[0]['value'] == 'reviewed'
    updated = await rule_service.update_rule(session, rule.id, test_workspace.id,
        RuleUpdate(conditions=[condition]))
    assert updated is not None
    assert updated.conditions[0]['value'] == 'reviewed'
    tx = Transaction(user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description='Literal preview', amount=Decimal('10'),
        currency='USD', date=date(2026, 10, 15), type='credit', source='import', review_status='reviewed')
    session.add(tx)
    await session.commit()
    preview = await rule_service.preview_rule(session, test_workspace.id, 'and', [condition.model_dump()],
        [RuleAction(op='set_reviewed', value=None)])
    assert preview.matched == 1 and preview.will_change == 0
    exported = await rule_service.export_rules(session, test_workspace.id)
    second = Workspace(name='Roundtrip destination', created_by_user_id=test_user.id)
    session.add(second)
    await session.commit()
    imported = await rule_service.import_rules(session, second.id, test_user.id, exported)
    assert imported.imported == 1
    restored = (await session.scalars(select(Rule).where(Rule.workspace_id == second.id))).one()
    assert restored.conditions[0]['value'] == 'reviewed'
    assert evaluate_conditions(restored.conditions_op, restored.conditions, tx)
    # Stored legacy literals also keep meaning without a data migration.
    assert evaluate_conditions('and', [{'field': 'review_status', 'op': 'equals', 'value': value}], tx)
    assert RuleCondition(field='description', op='equals', value=' reviewed ').value == ' reviewed '
    assert not evaluate_conditions('and', [{'field': 'description', 'op': 'equals', 'value': ' Literal preview '}], tx)
