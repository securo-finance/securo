"""Service-only actor provenance regression; no rule attribution policy selected.

The helper deliberately trusts callers for authorization (including previews).
Foreign-actor denial belongs to a separately approved service-boundary contract;
this file falsifies the already-documented no-half-stamps invariant instead.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.transaction import Transaction
from app.models.workspace import WorkspaceMember
from app.services.transaction_service import set_review_status


async def _make_transaction(session, user, account, workspace, *, status="needs_review"):
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="Synthetic actor provenance probe",
        amount=Decimal("10.00"),
        currency="USD",
        date=date(2026, 9, 26),
        type="debit",
        source="import",
        review_status=status,
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    return transaction


async def _stored_row(session, transaction_id):
    # Core reads bypass ORM identity-map state; compare every persisted column.
    row = (await session.execute(
        select(Transaction.__table__).where(Transaction.id == transaction_id)
    )).mappings().one()
    return dict(row)


@pytest.mark.asyncio
async def test_review_missing_actor_cannot_commit_timestamp_only_provenance(
    session, test_user, test_account, test_workspace
):
    transaction = await _make_transaction(session, test_user, test_account, test_workspace)
    transaction_id = transaction.id
    before = await _stored_row(session, transaction_id)
    assert before["review_status"] == "needs_review"
    assert before["reviewed_at"] is None and before["reviewed_by_id"] is None

    try:
        await set_review_status(
            session, test_workspace.id, transaction, "reviewed", actor_user_id=None
        )
    except ValueError:
        # A service rejection is acceptable, but it must precede mutation.
        # Do not roll back here: that would conceal a mutate-then-reject bug.
        pass
    # set_review_status never commits; exercise its real caller-owned boundary.
    await session.commit()
    after = await _stored_row(session, transaction_id)
    assert (after["reviewed_at"] is None) == (after["reviewed_by_id"] is None), (
        f"Missing actor committed half-populated provenance: before={before!r}; after={after!r}"
    )
    assert after == before, "An actorless explicit review must leave the existing row unchanged"


@pytest.mark.asyncio
async def test_review_actor_controls_preserve_authorized_and_legacy_provenance(
    session, test_user, test_account, test_workspace
):
    member = (await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == test_workspace.id,
            WorkspaceMember.user_id == test_user.id,
        )
    )).scalar_one()
    assert test_user.is_active and member.role == "owner"
    target = await _make_transaction(session, test_user, test_account, test_workspace)
    legacy = await _make_transaction(
        session, test_user, test_account, test_workspace, status="reviewed"
    )
    target_before = await _stored_row(session, target.id)
    legacy_before = await _stored_row(session, legacy.id)
    assert legacy_before["reviewed_at"] is None and legacy_before["reviewed_by_id"] is None

    assert await set_review_status(
        session, test_workspace.id, target, "reviewed", actor_user_id=test_user.id
    ) == 1
    assert await set_review_status(
        session, test_workspace.id, legacy, "reviewed", actor_user_id=None
    ) == 0
    await session.commit()
    target_after = await _stored_row(session, target.id)
    assert target_after["review_status"] == "reviewed"
    assert target_after["reviewed_at"] is not None
    assert target_after["reviewed_by_id"] == test_user.id
    assert target_after["review_attribution_kind"] == "human"
    assert target_after["review_rule_id"] is None
    assert {
        key: value for key, value in target_after.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by_id",
                       "review_attribution_kind", "review_rule_id"}
    } == {
        key: value for key, value in target_before.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by_id",
                       "review_attribution_kind", "review_rule_id"}
    }
    assert await _stored_row(session, legacy.id) == legacy_before


@pytest.mark.asyncio
async def test_initial_review_missing_actor_preserves_whole_row(
    session, test_user, test_account, test_workspace
):
    target = await _make_transaction(
        session, test_user, test_account, test_workspace, status="reviewed"
    )
    before = await _stored_row(session, target.id)
    assert before["reviewed_at"] is None and before["reviewed_by_id"] is None
    error = None
    try:
        await set_review_status(
            session, test_workspace.id, target, "reviewed",
            actor_user_id=None, initial=True,
        )
    except ValueError as exc:
        error = str(exc)
    await session.commit()  # Deliberately no rollback, even on rejected calls.
    after = await _stored_row(session, target.id)
    assert error == "Cannot mark transaction as reviewed without an actor"
    assert after == before


@pytest.mark.asyncio
async def test_mixed_review_missing_actor_preserves_every_row(
    session, test_user, test_account, test_workspace
):
    legacy = await _make_transaction(
        session, test_user, test_account, test_workspace, status="reviewed"
    )
    eligible = await _make_transaction(session, test_user, test_account, test_workspace)
    rows = [legacy, eligible]
    before = [await _stored_row(session, row.id) for row in rows]
    assert before[0]["review_status"] == "reviewed"
    assert before[1]["review_status"] == "needs_review"
    error = None
    try:
        await set_review_status(
            session, test_workspace.id, rows, "reviewed", actor_user_id=None
        )
    except ValueError as exc:
        error = str(exc)
    await session.commit()
    after = [await _stored_row(session, row.id) for row in rows]
    assert error == "Cannot mark transaction as reviewed without an actor"
    assert after == before


@pytest.mark.asyncio
async def test_actorless_unreview_clears_paired_stamps(
    session, test_user, test_account, test_workspace
):
    target = await _make_transaction(session, test_user, test_account, test_workspace)
    assert await set_review_status(
        session, test_workspace.id, target, "reviewed", actor_user_id=test_user.id
    ) == 1
    await session.commit()
    before = await _stored_row(session, target.id)
    assert before["reviewed_at"] is not None
    assert before["reviewed_by_id"] == test_user.id
    assert await set_review_status(
        session, test_workspace.id, target, "needs_review", actor_user_id=None
    ) == 1
    await session.commit()
    after = await _stored_row(session, target.id)
    assert after == {
        **before, "review_status": "needs_review",
        "reviewed_at": None, "reviewed_by_id": None,
        "review_attribution_kind": None, "review_rule_id": None,
    }


@pytest.mark.asyncio
async def test_foreign_workspace_preflight_preserves_entire_batch(
    session, test_user, test_account, test_workspace
):
    from app.models.account import Account
    from app.models.workspace import Workspace

    foreign_workspace = Workspace(
        id=uuid.uuid4(), name="Synthetic foreign workspace", kind="personal",
        created_by_user_id=test_user.id,
    )
    session.add(foreign_workspace)
    await session.flush()
    foreign_account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=foreign_workspace.id,
        name="Synthetic foreign account", type="checking", currency="USD",
        balance=Decimal("0.00"),
    )
    session.add(foreign_account)
    await session.commit()
    eligible = await _make_transaction(session, test_user, test_account, test_workspace)
    legacy = await _make_transaction(
        session, test_user, test_account, test_workspace, status="reviewed"
    )
    foreign = await _make_transaction(
        session, test_user, foreign_account, foreign_workspace
    )
    rows = [eligible, legacy, foreign]
    before = [await _stored_row(session, row.id) for row in rows]
    memory_before = [
        {column.key: getattr(row, column.key) for column in Transaction.__table__.columns}
        for row in rows
    ]
    with pytest.raises(ValueError, match="^Transaction not found$"):
        await set_review_status(
            session, test_workspace.id, rows, "reviewed", actor_user_id=test_user.id
        )
    memory_after = [
        {column.key: getattr(row, column.key) for column in Transaction.__table__.columns}
        for row in rows
    ]
    await session.commit()  # Persist any illicit mutation before asserting.
    after = [await _stored_row(session, row.id) for row in rows]
    assert memory_after == memory_before, "Preflight must not mutate in-memory targets"
    assert after == before


@pytest.mark.asyncio
async def test_nonnull_actor_mixed_batch_stamps_only_eligible_row(
    session, test_user, test_account, test_workspace
):
    eligible = await _make_transaction(session, test_user, test_account, test_workspace)
    legacy = await _make_transaction(
        session, test_user, test_account, test_workspace, status="reviewed"
    )
    eligible_before = await _stored_row(session, eligible.id)
    legacy_before = await _stored_row(session, legacy.id)
    assert await set_review_status(
        session, test_workspace.id, [eligible, legacy], "reviewed",
        actor_user_id=test_user.id,
    ) == 1
    await session.commit()
    after = await _stored_row(session, eligible.id)
    assert after["review_status"] == "reviewed"
    assert after["reviewed_at"] is not None and after["reviewed_by_id"] == test_user.id
    assert after == {
        **eligible_before, "review_status": "reviewed",
        "reviewed_at": after["reviewed_at"], "reviewed_by_id": test_user.id,
        "review_attribution_kind": "human", "review_rule_id": None,
    }
    assert await _stored_row(session, legacy.id) == legacy_before


@pytest.mark.asyncio
async def test_actorless_all_noop_batch_preserves_every_row(
    session, test_user, test_account, test_workspace
):
    rows = [
        await _make_transaction(
            session, test_user, test_account, test_workspace, status="reviewed"
        )
        for _ in range(2)
    ]
    before = [await _stored_row(session, row.id) for row in rows]
    assert all(row["reviewed_at"] is None and row["reviewed_by_id"] is None for row in before)
    assert await set_review_status(
        session, test_workspace.id, rows, "reviewed", actor_user_id=None
    ) == 0
    await session.commit()
    assert [await _stored_row(session, row.id) for row in rows] == before
