import io
import json
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import backup_service
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_STORAGE_PATH", tmp_path)
    yield tmp_path


@pytest.mark.asyncio
async def test_backup_preview_upload_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("backup.zip", b"not-a-zip", "application/zip")},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_can_preview_backup_upload(client: AsyncClient, viewer_auth_headers):
    response = await client.post(
        "/api/backups/preview-upload",
        headers=viewer_auth_headers,
        files={"file": ("backup.zip", b"not-a-zip", "application/zip")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_run_stored_backup(client: AsyncClient, viewer_auth_headers):
    response = await client.post(
        "/api/backups/run",
        headers=viewer_auth_headers,
        json={"content": "both"},
    )
    assert response.status_code == 403
    assert "read-only" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_backup_config_roundtrip(client: AsyncClient, auth_headers, backup_dir):
    default_resp = await client.get("/api/backups/config", headers=auth_headers)
    assert default_resp.status_code == 200
    assert default_resp.json()["scheduled_enabled"] is False
    assert "destination_path" not in default_resp.json()

    update_resp = await client.put(
        "/api/backups/config",
        json={
            "scheduled_enabled": True,
            "schedule": "weekly",
            "content": "configuration",
            "retention_count": 3,
            "retention_days": 30,
        },
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["scheduled_enabled"] is True
    assert body["schedule"] == "weekly"
    assert body["content"] == "configuration"
    assert body["retention_count"] == 3
    assert body["retention_days"] == 30

    rejected_resp = await client.put(
        "/api/backups/config",
        json={"destination_path": "/tmp/should-not-be-configurable"},
        headers=auth_headers,
    )
    assert rejected_resp.status_code == 422


@pytest.mark.asyncio
async def test_run_list_preview_and_download_stored_backup(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    test_account,
    test_transactions,
):
    run_resp = await client.post(
        "/api/backups/run",
        json={"content": "both"},
        headers=auth_headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    item = run_resp.json()
    assert item["id"].startswith("securo-backup-")
    assert item["entity_counts"]["accounts"] == 1
    assert item["entity_counts"]["transactions"] == len(test_transactions)

    list_resp = await client.get("/api/backups", headers=auth_headers)
    assert list_resp.status_code == 200
    assert [b["id"] for b in list_resp.json()] == [item["id"]]

    preview_resp = await client.get(f"/api/backups/{item['id']}/preview", headers=auth_headers)
    assert preview_resp.status_code == 200
    assert preview_resp.json()["valid"] is True
    assert preview_resp.json()["content"] == "both"

    download_resp = await client.get(f"/api/backups/{item['id']}/download", headers=auth_headers)
    assert download_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download_resp.content)) as zf:
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["content"] == "both"
        assert "transactions.json" in zf.namelist()


@pytest.mark.asyncio
async def test_preview_uploaded_backup(client: AsyncClient, auth_headers, test_account):
    export_resp = await client.get("/api/export/backup?content=data", headers=auth_headers)
    assert export_resp.status_code == 200

    preview_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("backup.zip", export_resp.content, "application/zip")},
        headers=auth_headers,
    )
    assert preview_resp.status_code == 200
    body = preview_resp.json()
    assert body["valid"] is True
    assert body["content"] == "data"
    assert body["entity_counts"]["accounts"] == 1


@pytest.mark.asyncio
async def test_restore_upload_creates_new_workspace_without_bank_connection(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    export_resp = await client.get("/api/export/backup?content=data", headers=auth_headers)
    assert export_resp.status_code == 200

    restore_resp = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", export_resp.content, "application/zip")},
        data={"content": "data", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restore_resp.status_code == 200, restore_resp.text
    body = restore_resp.json()
    restored_workspace_id = uuid.UUID(body["workspace_id"])
    assert body["mode"] == "new_workspace"
    assert body["workspace_name"].endswith("(restored)")
    assert restored_workspace_id != test_workspace.id

    account_count = await session.scalar(
        select(func.count()).select_from(Account).where(Account.workspace_id == restored_workspace_id)
    )
    tx_count = await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.workspace_id == restored_workspace_id)
    )
    assert account_count == 1
    assert tx_count == len(test_transactions)

    restored_account = (
        await session.execute(select(Account).where(Account.workspace_id == restored_workspace_id))
    ).scalar_one()
    assert restored_account.connection_id is None

    membership_count = await session.scalar(
        select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == restored_workspace_id)
    )
    assert membership_count == 1


@pytest.mark.asyncio
async def test_restore_stored_backup_to_current_workspace_requires_confirmation_then_restores(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    session: AsyncSession,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    run_resp = await client.post(
        "/api/backups/run",
        json={"content": "data"},
        headers=auth_headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    backup_id = run_resp.json()["id"]

    missing_confirm = await client.post(
        f"/api/backups/{backup_id}/restore",
        json={"content": "data", "mode": "current_workspace"},
        headers=auth_headers,
    )
    assert missing_confirm.status_code == 400

    restore_resp = await client.post(
        f"/api/backups/{backup_id}/restore",
        json={"content": "data", "mode": "current_workspace", "confirmation": "RESTORE"},
        headers=auth_headers,
    )
    assert restore_resp.status_code == 200, restore_resp.text
    body = restore_resp.json()
    assert body["workspace_id"] == str(test_workspace.id)
    assert body["restored_counts"]["transactions"] == len(test_transactions)

    workspace_id = test_workspace.id
    session.expire_all()
    account = (
        await session.execute(select(Account).where(Account.workspace_id == workspace_id))
    ).scalar_one()
    assert account.connection_id is None
