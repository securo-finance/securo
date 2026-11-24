"""Pockets in backups: the ledger travels with its account, never without it."""

import io
import json
import uuid
import zipfile
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.goal import Goal, GoalAllocation
from app.services import backup_service
from tests.test_goal_pockets import create_pocket, post_transaction

pytestmark = pytest.mark.asyncio


async def _pocket_with_assignment(client, auth_headers, account) -> tuple[dict, dict]:
    pocket = await create_pocket(client, auth_headers, account, initial="100")
    deposit = await post_transaction(
        client, auth_headers, account, "Deposit", "40", "credit",
        allocations=[(pocket["id"], "40")],
    )
    assert deposit.status_code == 201, deposit.text
    return pocket, deposit.json()


async def _ledger(session, workspace_id) -> list[tuple[str, Decimal]]:
    rows = await session.execute(
        select(GoalAllocation.source, GoalAllocation.amount)
        .where(GoalAllocation.workspace_id == workspace_id)
        .order_by(GoalAllocation.amount)
    )
    return [(source, amount) for source, amount in rows.all()]


async def test_the_backup_keeps_the_pocket_ledger(client, auth_headers, test_account):
    pocket, deposit = await _pocket_with_assignment(client, auth_headers, test_account)

    resp = await client.get("/api/export/backup", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        rows = json.loads(archive.read("goal_allocations.json"))
    assert sorted((row["source"], row["transaction_id"], row["amount"]) for row in rows) == [
        ("opening", None, "100.00"),
        ("transaction", deposit["id"], "40.00"),
    ]
    assert {row["goal_id"] for row in rows} == {pocket["id"]}


async def test_a_new_workspace_restore_brings_the_pocket_and_its_ledger(
    client, auth_headers, session, test_account
):
    await _pocket_with_assignment(client, auth_headers, test_account)
    export = await client.get("/api/export/backup?content=data", headers=auth_headers)
    assert export.status_code == 200, export.text

    restored = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", export.content, "application/zip")},
        data={"content": "data", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.text
    workspace_id = uuid.UUID(restored.json()["workspace_id"])

    pocket = await session.scalar(
        select(Goal).where(Goal.workspace_id == workspace_id, Goal.tracking_type == "pocket")
    )
    assert pocket is not None and pocket.account_id is not None
    assert await _ledger(session, workspace_id) == [
        ("transaction", Decimal("40.00")),
        ("opening", Decimal("100.00")),
    ]


async def test_a_configuration_restore_leaves_existing_pockets_alone(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    pocket, _ = await _pocket_with_assignment(client, auth_headers, test_account)
    workspace_id = test_workspace.id
    archive = await backup_service.build_backup_zip(
        session, test_workspace, content=backup_service.BackupContent.both
    )

    result = await backup_service.restore_backup_bytes(
        session,
        data=archive,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.configuration,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    assert await session.get(Goal, uuid.UUID(pocket["id"])) is not None
    pockets = await session.scalar(
        select(func.count()).select_from(Goal).where(
            Goal.workspace_id == workspace_id, Goal.tracking_type == "pocket"
        )
    )
    assert pockets == 1
    assert await _ledger(session, workspace_id) == [
        ("transaction", Decimal("40.00")),
        ("opening", Decimal("100.00")),
    ]
    assert any("pocket" in warning for warning in result.warnings)


async def test_restoring_a_backup_from_before_pockets_replaces_them_with_the_accounts(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    await _pocket_with_assignment(client, auth_headers, test_account)
    workspace_id = test_workspace.id
    archive = await backup_service.build_backup_zip(
        session, test_workspace, content=backup_service.BackupContent.data
    )
    # An archive written before Pockets existed has no ledger file.
    legacy = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(legacy, "w") as target:
        for item in source.infolist():
            if item.filename == "goal_allocations.json":
                continue
            data = source.read(item.filename)
            if item.filename == "goals.json":
                data = json.dumps(
                    [row for row in json.loads(data) if row["tracking_type"] != "pocket"]
                ).encode()
            target.writestr(item, data)

    await backup_service.restore_backup_bytes(
        session,
        data=legacy.getvalue(),
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    assert await _ledger(session, workspace_id) == []
    orphaned = await session.scalar(
        select(func.count()).select_from(Goal).where(
            Goal.workspace_id == workspace_id,
            Goal.tracking_type == "pocket",
            Goal.account_id.is_(None),
        )
    )
    assert orphaned == 0
