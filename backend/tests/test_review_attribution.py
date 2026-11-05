"""Persisted review attribution, legacy-shape, and transaction ownership regressions."""

import inspect
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.transaction_service import set_review_status


@pytest.mark.asyncio
async def test_explicit_human_review_persists_attribution_after_reload(
    session: AsyncSession, test_user, test_account, test_workspace, record_property
):
    record_property("transaction_service_origin", inspect.getfile(set_review_status))
    record_property("transaction_model_origin", inspect.getfile(Transaction))
    assert test_user.is_active
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Synthetic persisted human attribution tracer",
        amount=Decimal("10.00"),
        currency="USD",
        date=date(2026, 10, 15),
        type="debit",
        source="import",
        review_status="needs_review",
        created_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    transaction_id = transaction.id
    assert transaction.review_status == "needs_review"
    assert transaction.reviewed_at is None and transaction.reviewed_by_id is None

    # Exercise the accepted fifth-positional UUID compatibility boundary.
    assert await set_review_status(
        session, test_workspace.id, transaction, "reviewed", test_user.id
    ) == 1
    stamped_at = transaction.reviewed_at
    assert stamped_at is not None
    await session.commit()

    # Expiring/refreshing the same object would retain unmapped Python attributes.
    # Expunge only this transaction, then issue real SQL for a new ORM instance.
    session.expunge(transaction)
    reloaded = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert reloaded is not transaction
    assert reloaded.id == transaction_id
    assert reloaded.review_status == "reviewed"
    assert reloaded.reviewed_by_id == test_user.id
    assert reloaded.reviewed_at is not None
    # SQLite drops tzinfo; compare the existing stamp without inventing a restamp.
    assert reloaded.reviewed_at.replace(tzinfo=None) == stamped_at.replace(tzinfo=None)

    assert getattr(reloaded, "review_attribution_kind", None) == "human", (
        "Persisted explicit human review must have kind='human'"
    )
    assert getattr(reloaded, "review_rule_id", None) is None
    assert "review_attribution_kind" in Transaction.__table__.c
    assert "review_rule_id" in Transaction.__table__.c


async def _persist_attribution_target(session, user, account, workspace):
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="Synthetic executing rule attribution tracer",
        amount=Decimal("10.00"),
        currency="USD",
        date=date(2026, 10, 15),
        type="debit",
        source="import",
        review_status="needs_review",
        created_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    return transaction


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", [
    "apply_rules_to_transaction", "apply_single_rule", "apply_all_rules",
])
async def test_matching_rule_review_persists_executing_rule_attribution(
    session, test_user, test_account, test_workspace, driver
):
    from app.models.rule import Rule
    from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
    from app.services.rule_service import (
        apply_all_rules, apply_rules_to_transaction, apply_single_rule, create_rule,
    )

    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    transaction_id = transaction.id
    assert transaction.review_status == "needs_review"
    assert transaction.reviewed_at is None and transaction.reviewed_by_id is None
    rule = await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="persist executing rule identity",
            conditions_op="and",
            conditions=[RuleCondition(
                field="description", op="equals", value=transaction.description,
            )],
            actions=[RuleAction(op="set_reviewed", value=None)],
            is_active=True,
        ),
    )
    rule_id = rule.id
    session.expunge(rule)
    rule = (await session.execute(
        select(Rule).where(Rule.id == rule_id)
    )).scalar_one()
    assert isinstance(rule.id, uuid.UUID) and rule.id != rule.user_id
    assert rule.user_id == test_user.id and rule.workspace_id == test_workspace.id
    assert rule.is_active

    if driver == "apply_rules_to_transaction":
        # Retain user_id as rule-filter scope, never as review attestation.
        await apply_rules_to_transaction(session, test_user.id, transaction)
    elif driver == "apply_single_rule":
        assert await apply_single_rule(session, test_workspace.id, rule) == 1
    else:
        assert await apply_all_rules(session, test_workspace.id) == 1
    stamped_at = transaction.reviewed_at
    await session.commit()  # Also exercise the transaction driver's caller owner.
    session.expunge(transaction)
    reloaded = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert reloaded is not transaction and reloaded.id == transaction_id
    # The only matching persisted rule's set_reviewed action must really fire.
    assert reloaded.review_status == "reviewed"
    assert reloaded.reviewed_at is not None and stamped_at is not None
    assert reloaded.reviewed_at.replace(tzinfo=None) == stamped_at.replace(tzinfo=None)
    assert reloaded.reviewed_by_id is None, "Executing rules are not human reviewers"
    assert reloaded.review_attribution_kind == "rule"
    assert reloaded.review_rule_id == rule.id == rule_id


@pytest.mark.asyncio
async def test_malformed_legacy_actor_rejected_before_any_column_mutation(
    session, test_user, test_account, test_workspace
):
    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    before = {
        column.key: getattr(transaction, column.key)
        for column in Transaction.__table__.columns
    }
    rejected = False
    try:
        await set_review_status(
            session, test_workspace.id, transaction, "reviewed",
            actor_user_id='not-a-uuid',
        )
    except ValueError:
        rejected = True
    after = {
        column.key: getattr(transaction, column.key)
        for column in Transaction.__table__.columns
    }
    assert rejected, "Malformed legacy actor must raise ValueError before stamping"
    assert after == before, "Invalid UUID preflight must preserve every column"


# Expected on the frozen helper: assertion RED for both clears; NOT_RUN here.
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["human", "rule"])
async def test_needs_review_clears_all_four_persisted_attribution_fields(
    session, test_user, test_account, test_workspace, kind
):
    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    transaction_id = transaction.id
    reviewer_id = test_user.id if kind == "human" else None
    rule_id = uuid.uuid4() if kind == "rule" else None  # Deliberately no Rule FK.
    transaction.review_status = "reviewed"
    transaction.reviewed_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    transaction.reviewed_by_id = reviewer_id
    transaction.review_attribution_kind = kind
    transaction.review_rule_id = rule_id
    await session.commit()
    session.expunge(transaction)
    target = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert target is not transaction
    assert target.review_status == "reviewed" and target.reviewed_at is not None
    assert (target.reviewed_by_id, target.review_attribution_kind,
            target.review_rule_id) == (reviewer_id, kind, rule_id)

    await set_review_status(
        session, test_workspace.id, target, "needs_review", actor_user_id=None
    )
    await session.commit()
    session.expunge(target)
    reloaded = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert reloaded is not target and reloaded.id == transaction_id
    assert reloaded.review_status == "needs_review"
    assert (reloaded.reviewed_at, reloaded.reviewed_by_id,
            reloaded.review_attribution_kind, reloaded.review_rule_id) == (
        None, None, None, None
    ), "Clear must remove both old stamps and kind/rule attribution"


# Expected on the frozen helper: assertion RED for missing refusal; NOT_RUN here.
@pytest.mark.asyncio
async def test_initial_review_rejects_legacy_half_stamp_without_column_mutation(
    session, test_user, test_account, test_workspace
):
    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    transaction_id = transaction.id
    # NULL kind/rule admits this legacy half-stamp under the future CHECK.
    transaction.review_status = "needs_review"
    transaction.reviewed_at = None
    transaction.reviewed_by_id = test_user.id
    transaction.review_attribution_kind = None
    transaction.review_rule_id = None
    await session.commit()
    session.expunge(transaction)
    target = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert target is not transaction
    assert (target.review_status, target.reviewed_at, target.reviewed_by_id,
            target.review_attribution_kind, target.review_rule_id) == (
        "needs_review", None, test_user.id, None, None
    )
    before = {
        column.key: getattr(target, column.key)
        for column in Transaction.__table__.columns
    }
    rejected = False
    try:
        await set_review_status(
            session, test_workspace.id, target, "reviewed", test_user.id,
            initial=True,
        )
    except ValueError:
        rejected = True
    after = {
        column.key: getattr(target, column.key)
        for column in Transaction.__table__.columns
    }
    await session.commit()  # Persist any illicit mutation before asserting refusal.
    session.expunge(target)
    reloaded = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    persisted_after = {
        column.key: getattr(reloaded, column.key)
        for column in Transaction.__table__.columns
    }
    assert reloaded is not target
    assert rejected, "Initial review must reject nonempty legacy attribution"
    assert after == before, "Initial refusal must preserve every in-memory column"
    assert persisted_after == before, "Initial refusal must preserve every stored column"


async def _persist_preview_review_rule(session, user, workspace, transaction):
    from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
    from app.services.rule_service import create_rule

    rule = await create_rule(
        session, workspace.id, user.id,
        RuleCreate(
            name="preview executing rule identity",
            conditions_op="and",
            conditions=[RuleCondition(
                field="description", op="equals", value=transaction.description,
            )],
            actions=[RuleAction(op="set_reviewed", value=None)],
            is_active=True,
        ),
    )
    assert rule.is_active and rule.workspace_id == workspace.id
    assert rule.user_id == user.id and isinstance(rule.id, uuid.UUID)
    return rule


# Expected assertion RED on the current preview copy; neither case run here.
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["human", "rule"])
async def test_preview_preserves_existing_review_attribution_without_persistence(
    session, test_user, test_account, test_workspace, kind
):
    from sqlalchemy import inspect as sa_inspect
    from app.services.rule_service import preview_rules_for_transaction

    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    matching_rule = await _persist_preview_review_rule(
        session, test_user, test_workspace, transaction
    )
    previous_rule_id = uuid.uuid4() if kind == "rule" else None
    assert previous_rule_id != matching_rule.id
    transaction_id = transaction.id
    transaction.review_status = "reviewed"
    transaction.reviewed_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    transaction.reviewed_by_id = test_user.id if kind == "human" else None
    transaction.review_attribution_kind = kind
    transaction.review_rule_id = previous_rule_id
    await session.commit()
    session.expunge(transaction)
    original = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert original is not transaction
    assert original.review_status == "reviewed" and original.reviewed_at is not None
    assert (original.reviewed_by_id, original.review_attribution_kind,
            original.review_rule_id) == (
        test_user.id if kind == "human" else None, kind, previous_rule_id
    )
    attribution_before = (
        original.reviewed_at, original.reviewed_by_id,
        original.review_attribution_kind, original.review_rule_id,
    )
    stored_before = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == transaction_id)
    )).mappings().one())

    preview = await preview_rules_for_transaction(session, test_user.id, original)
    await session.commit()  # Persist any illicit original mutation, without rollback.
    stored_after = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == transaction_id)
    )).mappings().one())
    assert stored_after == stored_before, "Preview changed the persisted original"
    assert preview is not original
    preview_state = sa_inspect(preview)
    assert preview_state.transient and preview_state.session is None
    assert preview.review_status == "reviewed"
    assert (preview.reviewed_at, preview.reviewed_by_id,
            preview.review_attribution_kind, preview.review_rule_id) == attribution_before


@pytest.mark.asyncio
async def test_preview_predicts_executing_rule_attribution_without_persistence(
    session, test_user, test_account, test_workspace
):
    """Positive control: a real matching rule must transition only the preview."""
    from sqlalchemy import inspect as sa_inspect
    from app.services.rule_service import preview_rules_for_transaction

    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    matching_rule = await _persist_preview_review_rule(
        session, test_user, test_workspace, transaction
    )
    transaction_id = transaction.id
    executing_rule_id = matching_rule.id
    session.expunge(transaction)
    original = (await session.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )).scalar_one()
    assert original is not transaction and original.review_status == "needs_review"
    assert (original.reviewed_at, original.reviewed_by_id,
            original.review_attribution_kind, original.review_rule_id) == (
        None, None, None, None
    )
    stored_before = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == transaction_id)
    )).mappings().one())

    preview = await preview_rules_for_transaction(session, test_user.id, original)
    await session.commit()  # A successful prediction must not stamp the stored row.
    stored_after = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == transaction_id)
    )).mappings().one())
    assert stored_after == stored_before, "Preview persisted its predicted transition"
    assert preview is not original
    preview_state = sa_inspect(preview)
    assert preview_state.transient and preview_state.session is None
    assert preview.review_status == "reviewed" and preview.reviewed_at is not None
    assert preview.reviewed_by_id is None
    assert preview.review_attribution_kind == "rule"
    assert preview.review_rule_id == executing_rule_id


# HTTP read projection, v1.2: author-only; assertion RED is not yet observed.
# conftest.client binds ASGITransport to app.main.app, with no app-state rewrite.
# Its existing DB override and session use TestSessionLocal's shared StaticPool.
# auth_headers logs in test_user; X-Workspace-Id resolves its owner membership.
@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "human", "rule", "legacy_paired", "legacy_both_null", "legacy_half",
    "needs_review",
])
async def test_http_transaction_read_projects_stored_review_attribution(
    session, client, auth_headers, test_user, test_account, test_workspace, state
):
    from app.services.rule_service import apply_single_rule

    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    transaction_id = transaction.id
    assert transaction.review_status == "needs_review"
    assert (transaction.reviewed_at, transaction.reviewed_by_id,
            transaction.review_attribution_kind, transaction.review_rule_id) == (
        None, None, None, None
    )
    rule_id = None
    if state == "human":
        assert await set_review_status(
            session, test_workspace.id, transaction, "reviewed", test_user.id
        ) == 1
        assert transaction.reviewed_at is not None
        assert transaction.review_attribution_kind == "human"
    elif state == "rule":
        rule = await _persist_preview_review_rule(
            session, test_user, test_workspace, transaction
        )
        await session.refresh(rule)
        rule_id = rule.id
        assert rule.is_active and rule.workspace_id == test_workspace.id
        assert rule.user_id == test_user.id and rule_id != test_user.id
        assert await apply_single_rule(session, test_workspace.id, rule) == 1
    elif state.startswith("legacy_"):
        # Preserve ambiguous old stamps; never invoke a writer/backfill here.
        transaction.review_status = "reviewed"
        transaction.reviewed_at = (
            datetime(2026, 10, 15, tzinfo=timezone.utc)
            if state == "legacy_paired" else None
        )
        transaction.reviewed_by_id = (
            None if state == "legacy_both_null" else test_user.id
        )
        transaction.review_attribution_kind = None
        transaction.review_rule_id = None
    await session.commit()
    await session.refresh(transaction)
    expected_status = "needs_review" if state == "needs_review" else "reviewed"
    expected_kind = state if state in ("human", "rule") else None
    expected_user = (
        test_user.id if state in ("human", "legacy_paired", "legacy_half") else None
    )
    assert transaction.id == transaction_id
    assert transaction.workspace_id == test_workspace.id
    assert transaction.review_status == expected_status
    assert (transaction.review_attribution_kind, transaction.review_rule_id,
            transaction.reviewed_by_id) == (expected_kind, rule_id, expected_user)
    stamp = transaction.reviewed_at
    if state in ("human", "rule", "legacy_paired"):
        assert stamp is not None
    else:
        assert stamp is None
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    response = await client.get(f"/api/transactions/{transaction_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(transaction_id)
    assert body["user_id"] == str(test_user.id)
    assert body["review_status"] == expected_status
    assert body["reviewed_by_id"] == (str(expected_user) if expected_user else None)
    if stamp is None:
        assert body["reviewed_at"] is None
    else:
        assert isinstance(body["reviewed_at"], str) and body["reviewed_at"]
        assert datetime.fromisoformat(
            body["reviewed_at"].replace("Z", "+00:00")
        ).replace(tzinfo=None) == stamp.replace(tzinfo=None)
    # Only after a genuine 200/row/flat-stamp witness, check the future packet.
    expected = None
    if state == "human":
        expected = {
            "kind": "human", "reviewed_at": body["reviewed_at"],
            "user_id": str(test_user.id),
        }
    elif state == "rule":
        expected = {
            "kind": "rule", "reviewed_at": body["reviewed_at"],
            "rule_id": str(rule_id),
        }
    elif state.startswith("legacy_"):
        expected = {"kind": "unknown"}
    assert "review_attribution" in body, "Read packet must be present, even if null"
    assert body["review_attribution"] == expected


@pytest.mark.asyncio
async def test_http_review_ignores_forged_read_metadata_and_uses_authenticated_human(
    session, client, auth_headers, test_user, test_account, test_workspace
):
    # ReviewRequest(BaseModel) declares status only, with default extra=ignore.
    # This characterizes trusted server attribution, not an existing forgery bug.
    other_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    forged_rule_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    assert other_id != test_user.id and forged_rule_id != test_user.id
    transaction = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    await session.refresh(transaction)
    assert transaction.review_status == "needs_review"
    assert (transaction.reviewed_at, transaction.reviewed_by_id,
            transaction.review_attribution_kind, transaction.review_rule_id) == (
        None, None, None, None
    )
    transaction_id = transaction.id
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    response = await client.patch(
        f"/api/transactions/{transaction_id}/review", headers=headers,
        json={
            "review_status": "reviewed",
            "reviewed_by_id": str(other_id),
            "reviewed_at": "2000-01-01T00:00:00Z",
            "review_attribution_kind": "rule",
            "review_rule_id": str(forged_rule_id),
            "review_attribution": {
                "kind": "rule", "reviewed_at": "2000-01-01T00:00:00Z",
                "rule_id": str(forged_rule_id),
            },
        },
    )
    assert response.status_code == 200, response.text
    await session.refresh(transaction)
    assert transaction.id == transaction_id
    assert transaction.workspace_id == test_workspace.id
    assert transaction.review_status == "reviewed"
    assert transaction.reviewed_at is not None
    assert transaction.reviewed_at.year != 2000
    assert (transaction.reviewed_by_id, transaction.review_attribution_kind,
            transaction.review_rule_id) == (test_user.id, "human", None)
    read = await client.get(f"/api/transactions/{transaction_id}", headers=headers)
    assert read.status_code == 200, read.text
    for body in (response.json(), read.json()):
        assert body["id"] == str(transaction_id)
        assert body["review_status"] == "reviewed"
        assert body["reviewed_by_id"] == str(test_user.id)
        assert isinstance(body["reviewed_at"], str) and body["reviewed_at"]
        assert datetime.fromisoformat(
            body["reviewed_at"].replace("Z", "+00:00")
        ).replace(tzinfo=None) == transaction.reviewed_at.replace(tzinfo=None)
        assert "review_attribution" in body
        assert body["review_attribution"] == {
            "kind": "human", "reviewed_at": body["reviewed_at"],
            "user_id": str(test_user.id),
        }


# Metadata-created SQLite CHECK family; source-only authoring, NOT_RUN.
# This is not migration, PostgreSQL FK/deleted-rule, or activation evidence.
_CHECK_STAMP = datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc)
_CHECK_RULE_UUID = uuid.UUID("a2222222-2222-4222-8222-222222222222")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status,stamp,has_user,rule_id", [
    pytest.param(None, "reviewed", _CHECK_STAMP, False, _CHECK_RULE_UUID,
                 id="null_kind_with_rule_id"),
    pytest.param("robot", "reviewed", _CHECK_STAMP, False, None,
                 id="unknown_kind_robot"),
    pytest.param("human", "reviewed", None, True, None,
                 id="human_missing_time"),
    pytest.param("human", "reviewed", _CHECK_STAMP, False, None,
                 id="human_missing_user"),
    pytest.param("human", "reviewed", _CHECK_STAMP, True, _CHECK_RULE_UUID,
                 id="human_with_rule_id"),
    pytest.param("human", "needs_review", _CHECK_STAMP, True, None,
                 id="human_needs_review"),
    pytest.param("rule", "reviewed", _CHECK_STAMP, False, None,
                 id="rule_missing_rule_id"),
    pytest.param("rule", "reviewed", _CHECK_STAMP, True, _CHECK_RULE_UUID,
                 id="rule_with_user"),
    pytest.param("rule", "reviewed", None, False, _CHECK_RULE_UUID,
                 id="rule_missing_time"),
    pytest.param("rule", "needs_review", _CHECK_STAMP, False, _CHECK_RULE_UUID,
                 id="rule_needs_review"),
])
async def test_sqlite_metadata_check_rejects_invalid_review_attribution_shape(
    session: AsyncSession, test_user, test_account, test_workspace,
    kind, status, stamp, has_user, rule_id,
):
    from sqlalchemy.exc import IntegrityError

    assert session.get_bind().dialect.name == "sqlite"
    target = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    # Cache all fixture identities before rollback can expire their ORM state.
    target_id, user_id = target.id, test_user.id
    account_id, workspace_id = test_account.id, test_workspace.id
    before = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == target_id)
    )).mappings().one())
    assert (before["id"], before["user_id"], before["account_id"],
            before["workspace_id"]) == (target_id, user_id, account_id, workspace_id)
    assert before["review_status"] == "needs_review"
    assert (before["reviewed_at"], before["reviewed_by_id"],
            before["review_attribution_kind"], before["review_rule_id"]) == (
        None, None, None, None
    )

    primary_failure = None
    try:
        target.review_status = status
        target.reviewed_at = stamp
        target.reviewed_by_id = user_id if has_user else None
        target.review_attribution_kind = kind
        target.review_rule_id = rule_id
        # No helper guard: the actual DB flush must reject precisely this CHECK.
        with pytest.raises(IntegrityError) as rejected:
            await session.flush()
        assert str(rejected.value.orig) == (
            "CHECK constraint failed: ck_transactions_review_attribution_shape"
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        # Also undo unexpected acceptance; never commit an invalid candidate.
        # A rollback error must not mask the original SQL/assertion failure.
        try:
            await session.rollback()
        except BaseException as rollback_exc:
            if primary_failure is None:
                raise
            primary_failure.add_note(f"Rollback also failed: {rollback_exc!r}")

    # Only successful exact-CHECK rejection reaches this fresh whole-row oracle.
    # Do not read target or fixture ORM attributes after rollback.
    after = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == target_id)
    )).mappings().one())
    assert after == before, "CHECK rejection must preserve every stored column"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status,stamp,has_user,rule_id", [
    pytest.param(None, "reviewed", _CHECK_STAMP, True, None,
                 id="legacy_reviewed_paired"),
    pytest.param(None, "reviewed", None, False, None,
                 id="legacy_reviewed_both_null"),
    pytest.param(None, "reviewed", _CHECK_STAMP, False, None,
                 id="legacy_reviewed_time_only"),
    pytest.param(None, "reviewed", None, True, None,
                 id="legacy_reviewed_user_only"),
    pytest.param(None, "needs_review", _CHECK_STAMP, True, None,
                 id="legacy_needs_review_paired"),
    pytest.param(None, "needs_review", None, False, None,
                 id="legacy_needs_review_both_null"),
    pytest.param(None, "needs_review", _CHECK_STAMP, False, None,
                 id="legacy_needs_review_time_only"),
    pytest.param(None, "needs_review", None, True, None,
                 id="legacy_needs_review_user_only"),
    pytest.param("human", "reviewed", _CHECK_STAMP, True, None,
                 id="v2_complete_human"),
    pytest.param("rule", "reviewed", _CHECK_STAMP, False, _CHECK_RULE_UUID,
                 id="v2_complete_rule_no_rule_row"),
])
async def test_sqlite_metadata_check_accepts_legacy_and_complete_review_shapes(
    session: AsyncSession, test_user, test_account, test_workspace,
    kind, status, stamp, has_user, rule_id,
):
    assert session.get_bind().dialect.name == "sqlite"
    # Metadata proves no declared Rule FK; SQLite's global FK-off fixture does
    # not prove PostgreSQL no-FK/deleted-rule behavior (later native gate).
    assert not Transaction.__table__.c.review_rule_id.foreign_keys
    target = await _persist_attribution_target(
        session, test_user, test_account, test_workspace
    )
    target_id, user_id = target.id, test_user.id
    account_id, workspace_id = test_account.id, test_workspace.id
    before = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == target_id)
    )).mappings().one())
    assert (before["id"], before["user_id"], before["account_id"],
            before["workspace_id"]) == (target_id, user_id, account_id, workspace_id)
    assert before["review_status"] == "needs_review"
    assert (before["reviewed_at"], before["reviewed_by_id"],
            before["review_attribution_kind"], before["review_rule_id"]) == (
        None, None, None, None
    )
    # Explicit native datetime/UUID values; the human is the real test_user.
    # No Rule is created for the synthetic rule UUID in this independent case.
    shape = {
        "review_status": status,
        "reviewed_at": stamp,
        "reviewed_by_id": user_id if has_user else None,
        "review_attribution_kind": kind,
        "review_rule_id": rule_id,
    }
    for field, value in shape.items():
        setattr(target, field, value)
    await session.flush()
    await session.commit()
    # Table mappings bypass the identity map and any read-property inference.
    after = dict((await session.execute(
        select(Transaction.__table__).where(Transaction.id == target_id)
    )).mappings().one())
    expected = {**before, **shape}
    # SQLite's DateTime result has no tzinfo; do not synthesize a new timestamp.
    expected["reviewed_at"] = stamp.replace(tzinfo=None) if stamp else None
    assert after == expected, "Accepted shape must persist without repair/inference"
