"""Backups must not carry reconciliation rows whose target is gone (#1780)."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationEvent, ReconciliationSuggestion
from app.services import backup_service
from app.services.backup_service import RestoreIdMaps, _rewrite_polymorphic_reference


def _suggestion(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
    expectation_id: uuid.UUID,
) -> ReconciliationSuggestion:
    return ReconciliationSuggestion(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        expectation_kind="transaction",
        expectation_id=expectation_id,
        strategy_id="rule-1",
        node="amount",
        amount=Decimal("10.00"),
        status="pending",
        created_at=datetime.now(timezone.utc),
        resolved_by=user_id,
    )


def _event(
    workspace_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
    expectation_id: uuid.UUID,
) -> ReconciliationEvent:
    return ReconciliationEvent(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        at=datetime.now(timezone.utc),
        action="linked",
        transaction_id=transaction_id,
        expectation_kind="transaction",
        expectation_id=expectation_id,
        amount=Decimal("10.00"),
        detail={},
    )


@pytest.mark.asyncio
async def test_export_skips_a_suggestion_whose_expectation_is_missing(
    session: AsyncSession, test_user, test_workspace, test_transactions
):
    transaction = test_transactions[0]
    transaction.workspace_id = test_workspace.id
    valid = _suggestion(
        test_workspace.id, test_user.id, transaction.id, transaction.id
    )
    orphan = _suggestion(
        test_workspace.id, test_user.id, transaction.id, uuid.uuid4()
    )
    session.add_all([valid, orphan])
    await session.commit()

    rows = await backup_service._fetch_entity_rows(
        session, "reconciliation_suggestions", test_workspace.id
    )

    assert [row["id"] for row in rows] == [str(valid.id)]


@pytest.mark.asyncio
async def test_export_keeps_a_reconciliation_event_whose_target_is_missing(
    session: AsyncSession, test_user, test_workspace, test_transactions
):
    # History deliberately outlives the transaction, invoice or recurring bill
    # it points at, so an orphan event still travels in the archive and restore
    # gives it an opaque reference.
    transaction = test_transactions[0]
    transaction.workspace_id = test_workspace.id
    valid = _event(test_workspace.id, transaction.id, transaction.id)
    orphan = _event(test_workspace.id, None, uuid.uuid4())
    session.add_all([valid, orphan])
    await session.commit()

    rows = await backup_service._fetch_entity_rows(
        session, "reconciliation_events", test_workspace.id
    )

    assert sorted(row["id"] for row in rows) == sorted([str(valid.id), str(orphan.id)])


def test_a_pending_suggestion_with_a_missing_target_is_expired_on_restore():
    missing_transaction = uuid.uuid4()
    row = {"status": "pending", "expectation_kind": "transaction"}
    id_maps = RestoreIdMaps(by_table={}, flat_entities={}, groupings={}, opaque_references={})
    column = ReconciliationSuggestion.__table__.c.expectation_id

    rewritten = _rewrite_polymorphic_reference(
        ReconciliationSuggestion,
        column,
        row,
        missing_transaction,
        id_maps=id_maps,
        owned_reference_ids={},
    )

    assert row["status"] == "expired"
    assert rewritten != str(missing_transaction)
    uuid.UUID(rewritten)


def test_restore_expires_an_orphan_suggestion_whatever_the_key_order():
    # The archive decides the order the columns are read in, and the expiry is
    # decided while expectation_id is rewritten. Reading status before that
    # would restore the suggestion as pending with a target nothing answers.
    user_id = uuid.uuid4()
    suggestion = _suggestion(uuid.uuid4(), user_id, None, uuid.uuid4())
    row = backup_service._serialize_model(suggestion)
    rest = [key for key in row if key not in {"status", "expectation_id"}]

    statuses = []
    for status_first in (True, False):
        ordered = (
            ["status", *rest, "expectation_id"]
            if status_first
            else [*rest, "expectation_id", "status"]
        )
        data = backup_service._prepare_model_data(
            ReconciliationSuggestion,
            {key: row[key] for key in ordered},
            id_maps=RestoreIdMaps(
                by_table={}, flat_entities={}, groupings={}, opaque_references={}
            ),
            target_workspace_id=uuid.uuid4(),
            target_user_id=user_id,
            remap=True,
            owned_reference_ids={},
            workspace_user_ids={str(user_id)},
        )
        statuses.append(data["status"])

    assert statuses == ["expired", "expired"]
