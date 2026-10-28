import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.category import Category
from app.models.group import Group
from app.models.invoice import Invoice, InvoiceLine, InvoiceSettings
from app.models.invoice_attachment import InvoiceAttachment
from app.models.invoice_schedule import InvoiceSchedule
from app.models.payee import Payee
from app.models.product import Product, ProductPrice
from app.models.reconciliation import ReconciliationEvent, ReconciliationSuggestion
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers import get_storage_provider
from app.services import backup_service, invoice_logo_service


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

    clear_days_resp = await client.put(
        "/api/backups/config",
        json={"retention_days": None},
        headers=auth_headers,
    )
    assert clear_days_resp.status_code == 200
    assert clear_days_resp.json()["retention_days"] is None

    rejected_resp = await client.put(
        "/api/backups/config",
        json={"destination_path": "/tmp/should-not-be-configurable"},
        headers=auth_headers,
    )
    assert rejected_resp.status_code == 422


@pytest.mark.asyncio
async def test_scheduled_backups_require_shared_storage(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    session: AsyncSession,
    monkeypatch,
):
    enabled_resp = await client.put(
        "/api/backups/config",
        json={"scheduled_enabled": True},
        headers=auth_headers,
    )
    assert enabled_resp.status_code == 200

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_storage_shared", False)

    config_resp = await client.get("/api/backups/config", headers=auth_headers)
    assert config_resp.status_code == 200
    assert config_resp.json()["storage_shared"] is False

    update_resp = await client.put(
        "/api/backups/config",
        json={"scheduled_enabled": False, "retention_count": 5},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["storage_shared"] is False

    rejected_resp = await client.put(
        "/api/backups/config",
        json={"scheduled_enabled": True},
        headers=auth_headers,
    )
    assert rejected_resp.status_code == 400
    assert "shared persistent backup storage" in rejected_resp.json()["detail"]

    run_result = await backup_service.run_scheduled_backups(session)
    assert run_result == {"created": 0, "skipped": 0, "errors": []}
    assert list(backup_dir.glob("*.zip")) == []


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
async def test_list_and_preview_read_only_archive_metadata(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    monkeypatch,
):
    run_resp = await client.post(
        "/api/backups/run",
        json={"content": "configuration"},
        headers=auth_headers,
    )
    assert run_resp.status_code == 200
    backup_id = run_resp.json()["id"]

    def fail_full_read(_path: Path) -> bytes:
        raise AssertionError("list/preview must not read the entire backup archive")

    monkeypatch.setattr(Path, "read_bytes", fail_full_read)
    list_resp = await client.get("/api/backups", headers=auth_headers)
    assert list_resp.status_code == 200
    assert [item["id"] for item in list_resp.json()] == [backup_id]

    preview_resp = await client.get(
        f"/api/backups/{backup_id}/preview",
        headers=auth_headers,
    )
    assert preview_resp.status_code == 200
    assert preview_resp.json()["content"] == "configuration"


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
async def test_preview_rejects_encrypted_and_non_object_backups(client: AsyncClient, auth_headers):
    metadata = {
        "format_version": backup_service.FORMAT_VERSION,
        "content": "both",
        "entity_counts": {},
    }
    encrypted = backup_service.build_backup_archive(
        {"metadata.json": metadata}, password="correct horse battery staple"
    )
    encrypted_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("encrypted.zip", encrypted, "application/zip")},
        headers=auth_headers,
    )
    assert encrypted_resp.status_code == 400
    assert "encrypted backups cannot be restored" in encrypted_resp.json()["detail"].lower()

    non_object = backup_service.build_backup_archive({"metadata.json": []})
    non_object_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("invalid.zip", non_object, "application/zip")},
        headers=auth_headers,
    )
    assert non_object_resp.status_code == 400
    assert "json object" in non_object_resp.json()["detail"].lower()

    numeric_date = backup_service.build_backup_archive(
        {"metadata.json": {**metadata, "export_date": 123}}
    )
    numeric_date_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("numeric-date.zip", numeric_date, "application/zip")},
        headers=auth_headers,
    )
    assert numeric_date_resp.status_code == 200
    assert numeric_date_resp.json()["export_date"] is None

    invalid_counts = backup_service.build_backup_archive(
        {"metadata.json": {**metadata, "entity_counts": {"accounts": "many"}}}
    )
    invalid_counts_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("invalid-counts.zip", invalid_counts, "application/zip")},
        headers=auth_headers,
    )
    assert invalid_counts_resp.status_code == 400
    assert "invalid backup metadata" in invalid_counts_resp.json()["detail"].lower()

    invalid_name = backup_service.build_backup_archive(
        {"metadata.json": {**metadata, "workspace_name": 123}}
    )
    invalid_name_resp = await client.post(
        "/api/backups/preview-upload",
        files={"file": ("invalid-name.zip", invalid_name, "application/zip")},
        headers=auth_headers,
    )
    assert invalid_name_resp.status_code == 400
    assert "invalid backup metadata" in invalid_name_resp.json()["detail"].lower()


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
        select(func.count())
        .select_from(Account)
        .where(Account.workspace_id == restored_workspace_id)
    )
    tx_count = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.workspace_id == restored_workspace_id)
    )
    assert account_count == 1
    assert tx_count == len(test_transactions)

    restored_account = (
        await session.execute(select(Account).where(Account.workspace_id == restored_workspace_id))
    ).scalar_one()
    assert restored_account.connection_id is None

    membership_count = await session.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == restored_workspace_id)
    )
    assert membership_count == 1


@pytest.mark.asyncio
async def test_new_workspace_restore_preserves_grouping_and_avoids_user_scoped_collisions(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    source_pair_id = uuid.uuid4()
    test_transactions[0].transfer_pair_id = source_pair_id
    test_transactions[1].transfer_pair_id = source_pair_id

    recurring = RecurringTransaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Workspace-scoped recurring payment",
        amount=Decimal("25.00"),
        currency="USD",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_occurrence=date(2026, 11, 1),
    )
    synced_group = AssetGroup(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced wallet",
        source="test-provider",
        external_id="provider-wallet-1",
    )
    user_group = Group(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Workspace-scoped group",
    )
    secured_asset = Asset(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Restored property",
        type="real_estate",
        currency="USD",
        source="manual",
    )
    source_suggestion_group_id = uuid.uuid4()
    suggestions = [
        ReconciliationSuggestion(
            workspace_id=test_workspace.id,
            transaction_id=transaction.id,
            expectation_kind="transaction",
            expectation_id=expectation.id,
            group_id=source_suggestion_group_id,
            strategy_id="test-strategy",
            node="transaction",
            amount=transaction.amount,
            scores={},
            status="pending",
        )
        for transaction, expectation in zip(test_transactions[:2], test_transactions[2:4])
    ]
    orphan_expectation_id = uuid.uuid4()
    historical_suggestion = ReconciliationSuggestion(
        workspace_id=test_workspace.id,
        transaction_id=test_transactions[2].id,
        expectation_kind="invoice",
        expectation_id=orphan_expectation_id,
        strategy_id="historical-strategy",
        node="invoice",
        amount=test_transactions[2].amount,
        scores={},
        status="declined",
    )
    historical_event = ReconciliationEvent(
        workspace_id=test_workspace.id,
        action="declined",
        transaction_id=test_transactions[2].id,
        expectation_kind="invoice",
        expectation_id=orphan_expectation_id,
        amount=test_transactions[2].amount,
        strategy_id="historical-strategy",
        user_id=test_user.id,
        detail={},
    )
    session.add_all(
        [
            recurring,
            synced_group,
            user_group,
            secured_asset,
            *suggestions,
            historical_suggestion,
            historical_event,
        ]
    )
    await session.flush()
    test_account.secured_asset_id = secured_asset.id
    source_account_id = test_account.id
    source_asset_id = secured_asset.id
    source_user_group_name = user_group.name
    await session.commit()

    data = await backup_service.build_backup_zip(
        session,
        test_workspace,
        content=backup_service.BackupContent.data,
    )
    result = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )

    restored_recurring = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.workspace_id == result.workspace_id
            )
        )
    ).scalar_one()
    restored_group = (
        await session.execute(
            select(AssetGroup).where(AssetGroup.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    restored_user_group = (
        await session.execute(select(Group).where(Group.workspace_id == result.workspace_id))
    ).scalar_one()
    restored_account = (
        await session.execute(select(Account).where(Account.workspace_id == result.workspace_id))
    ).scalar_one()
    restored_asset = (
        await session.execute(select(Asset).where(Asset.workspace_id == result.workspace_id))
    ).scalar_one()
    restored_transactions = list(
        (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.workspace_id == result.workspace_id,
                    Transaction.transfer_pair_id.is_not(None),
                )
                .order_by(Transaction.id)
            )
        ).scalars()
    )
    restored_suggestions = list(
        (
            await session.execute(
                select(ReconciliationSuggestion)
                .where(
                    ReconciliationSuggestion.workspace_id == result.workspace_id,
                    ReconciliationSuggestion.group_id.is_not(None),
                )
                .order_by(ReconciliationSuggestion.id)
            )
        ).scalars()
    )
    restored_historical_suggestion = (
        await session.execute(
            select(ReconciliationSuggestion).where(
                ReconciliationSuggestion.workspace_id == result.workspace_id,
                ReconciliationSuggestion.status == "declined",
            )
        )
    ).scalar_one()
    restored_historical_event = (
        await session.execute(
            select(ReconciliationEvent).where(
                ReconciliationEvent.workspace_id == result.workspace_id
            )
        )
    ).scalar_one()

    assert restored_recurring.account_id != source_account_id
    assert restored_group.external_id is None
    assert restored_user_group.name == source_user_group_name
    assert restored_account.secured_asset_id == restored_asset.id
    assert restored_asset.id != source_asset_id
    assert len(restored_transactions) == 2
    assert restored_transactions[0].transfer_pair_id == restored_transactions[1].transfer_pair_id
    assert restored_transactions[0].transfer_pair_id != source_pair_id
    assert len(restored_suggestions) == 2
    assert restored_suggestions[0].group_id == restored_suggestions[1].group_id
    assert restored_suggestions[0].group_id != source_suggestion_group_id
    assert restored_historical_suggestion.expectation_id == (
        restored_historical_event.expectation_id
    )
    assert restored_historical_suggestion.expectation_id != orphan_expectation_id


@pytest.mark.asyncio
async def test_current_restore_rejects_cross_workspace_references_but_keeps_owned_ones(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    foreign_workspace = Workspace(
        name="Foreign workspace",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(foreign_workspace)
    await session.flush()
    foreign_account = Account(
        user_id=test_user.id,
        workspace_id=foreign_workspace.id,
        name="Foreign account",
        type="checking",
        balance=Decimal("0.00"),
        currency="USD",
    )
    foreign_category = Category(
        user_id=test_user.id,
        workspace_id=foreign_workspace.id,
        name="Foreign category",
    )
    session.add_all([foreign_account, foreign_category])
    await session.commit()
    foreign_account_id = foreign_account.id

    source_transaction = test_transactions[0]
    source_transaction_id = source_transaction.id
    test_account_id = test_account.id
    test_user_id = test_user.id
    test_workspace_id = test_workspace.id
    test_workspace_name = test_workspace.name
    transaction_row = backup_service._serialize_model(source_transaction)
    transaction_row["category_id"] = str(foreign_category.id)
    metadata = {
        "format_version": backup_service.FORMAT_VERSION,
        "content": "data",
        "workspace_id": str(test_workspace_id),
        "workspace_name": test_workspace_name,
        "entity_counts": {"transactions": 1},
        "warnings": [],
    }
    archive = backup_service.build_backup_archive(
        {
            "metadata.json": metadata,
            "transactions.json": [transaction_row],
        }
    )

    await backup_service.restore_backup_bytes(
        session,
        data=archive,
        target_user_id=test_user_id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    restored = await session.get(Transaction, source_transaction_id)
    assert restored is not None
    assert restored.account_id == test_account_id
    assert restored.category_id is None

    current_workspace = await session.get(Workspace, test_workspace_id)
    assert current_workspace is not None
    transaction_row["account_id"] = str(foreign_account_id)
    # A grouping UUID must not act as an untyped allow-list entry for a
    # foreign-key value that happens to use the same UUID.
    transaction_row["transfer_pair_id"] = str(foreign_account_id)
    malicious_archive = backup_service.build_backup_archive(
        {
            "metadata.json": metadata,
            "transactions.json": [transaction_row],
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        await backup_service.restore_backup_bytes(
            session,
            data=malicious_archive,
            target_user_id=test_user_id,
            current_workspace=current_workspace,
            content=backup_service.BackupContent.data,
            mode=backup_service.BackupRestoreMode.current_workspace,
            confirmation="RESTORE",
        )

    assert exc_info.value.status_code == 400
    assert "account_id" in exc_info.value.detail
    session.expire_all()
    preserved = await session.get(Transaction, source_transaction_id)
    assert preserved is not None
    assert preserved.account_id == test_account_id


@pytest.mark.asyncio
async def test_restore_associations_do_not_trust_unrelated_grouping_ids(
    session: AsyncSession,
):
    account_id = str(uuid.uuid4())
    foreign_collection_id = str(uuid.uuid4())
    id_maps = backup_service.RestoreIdMaps(
        by_table={"accounts": {account_id: account_id}},
        flat_entities={account_id: account_id},
        groupings={
            ("transactions", "transfer_pair_id"): {foreign_collection_id: foreign_collection_id}
        },
        opaque_references={},
    )

    with pytest.raises(ValueError, match="collection_id"):
        await backup_service._insert_associations(
            session,
            "collection_accounts",
            [
                {
                    "collection_id": foreign_collection_id,
                    "account_id": account_id,
                }
            ],
            id_maps,
            {"accounts": {account_id}},
        )


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


@pytest.mark.asyncio
async def test_current_restore_only_replaces_entities_present_in_archive(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    rule = Rule(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Keep this rule",
        conditions_op="and",
        conditions=[],
        actions=[],
    )
    session.add(rule)
    await session.commit()
    rule_id = rule.id

    data = await backup_service.build_backup_zip(
        session,
        test_workspace,
        content=backup_service.BackupContent.data,
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "rules.json" not in zf.namelist()

    result = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    assert await session.get(Rule, rule_id) is not None
    assert "rules" not in result.restored_counts
    assert any("rules" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_configuration_restore_preserves_data_references(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    category = Category(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Preserved category",
    )
    payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Preserved payee",
    )
    product = Product(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Preserved product",
    )
    session.add_all([category, payee, product])
    await session.flush()

    recurring = RecurringTransaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        category_id=category.id,
        description="Monthly service",
        amount=Decimal("25.00"),
        currency="USD",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_occurrence=date(2026, 11, 1),
    )
    schedule = InvoiceSchedule(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        payee_id=payee.id,
        name="Monthly retainer",
        frequency="monthly",
        start_date=date(2026, 1, 1),
        currency="USD",
    )
    price = ProductPrice(
        product_id=product.id,
        workspace_id=test_workspace.id,
        currency="USD",
        unit_price=Decimal("50.00"),
    )
    session.add_all([recurring, schedule, price])
    await session.flush()

    transaction = test_transactions[0]
    transaction.category_id = category.id
    transaction.payee_id = payee.id
    transaction.recurring_transaction_id = recurring.id
    invoice = Invoice(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        payee_id=payee.id,
        issue_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        currency="USD",
        total=Decimal("50.00"),
        schedule_id=schedule.id,
        sequence=10,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    )
    session.add(invoice)
    await session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        workspace_id=test_workspace.id,
        description="Monthly retainer",
        unit_price=Decimal("50.00"),
        total=Decimal("50.00"),
        product_id=product.id,
        price_id=price.id,
    )
    session.add(line)
    await session.flush()
    transaction_id = transaction.id
    invoice_id = invoice.id
    line_id = line.id
    schedule_id = schedule.id
    category_id = category.id
    payee_id = payee.id
    recurring_id = recurring.id
    product_id = product.id
    price_id = price.id
    await session.commit()

    data = await backup_service.build_backup_zip(
        session,
        test_workspace,
        content=backup_service.BackupContent.configuration,
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "transactions.json" not in zf.namelist()
        assert "invoices.json" not in zf.namelist()
        assert "invoice_lines.json" not in zf.namelist()

    await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.configuration,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    restored_transaction = await session.get(Transaction, transaction_id)
    restored_invoice = await session.get(Invoice, invoice_id)
    restored_line = await session.get(InvoiceLine, line_id)
    restored_schedule = await session.get(InvoiceSchedule, schedule_id)
    assert restored_transaction is not None
    assert restored_transaction.category_id == category_id
    assert restored_transaction.payee_id == payee_id
    assert restored_transaction.recurring_transaction_id == recurring_id
    assert restored_invoice is not None
    assert restored_invoice.payee_id == payee_id
    assert restored_invoice.schedule_id == schedule_id
    assert restored_invoice.sequence == 10
    assert restored_invoice.period_start == date(2026, 10, 1)
    assert restored_invoice.period_end == date(2026, 10, 31)
    assert restored_line is not None
    assert restored_line.product_id == product_id
    assert restored_line.price_id == price_id
    assert restored_schedule is not None
    assert restored_schedule.payee_id == payee_id


@pytest.mark.asyncio
async def test_failed_current_restore_keeps_existing_attachment_file(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    storage = get_storage_provider()
    transaction = test_transactions[0]
    storage_key = f"{test_workspace.id}/{transaction.id}/before-restore.txt"
    attachment_data = b"must survive rollback"
    stored = await storage.upload(storage_key, attachment_data, "text/plain")
    logo_id = uuid.uuid4()
    logo_key = invoice_logo_service.storage_key(test_workspace.id, logo_id)
    old_logo_data = b"must also survive rollback"
    restored_logo_data = b"incoming logo must be rolled back"
    await storage.upload(logo_key, old_logo_data, "image/png")
    attachment = TransactionAttachment(
        transaction_id=transaction.id,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        filename="before-restore.txt",
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        size=stored.size,
    )
    invoice_settings = InvoiceSettings(
        workspace_id=test_workspace.id,
        logo_id=logo_id,
        issuer_display_name="Existing issuer",
    )
    session.add_all([attachment, invoice_settings])
    await session.commit()
    attachment_id = attachment.id

    valid_data = await backup_service.build_backup_zip(
        session,
        test_workspace,
        content=backup_service.BackupContent.data,
    )
    corrupted = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(valid_data)) as source,
        zipfile.ZipFile(corrupted, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == f"invoice_logos/{logo_id}.png":
                payload = restored_logo_data
            if info.filename == "goals.json":
                payload = json.dumps([{"id": "not-a-uuid"}]).encode()
            target.writestr(info, payload)

    with pytest.raises(HTTPException) as exc_info:
        await backup_service.restore_backup_bytes(
            session,
            data=corrupted.getvalue(),
            target_user_id=test_user.id,
            current_workspace=test_workspace,
            content=backup_service.BackupContent.data,
            mode=backup_service.BackupRestoreMode.current_workspace,
            confirmation="RESTORE",
        )

    assert exc_info.value.status_code == 400
    session.expire_all()
    assert await session.get(TransactionAttachment, attachment_id) is not None
    assert await storage.download(storage_key) == attachment_data
    assert await storage.download(logo_key) == old_logo_data


@pytest.mark.asyncio
async def test_current_restore_preserves_attachment_metadata_when_file_is_unavailable(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_transactions: list[Transaction],
):
    storage = get_storage_provider()
    stored = await storage.upload(
        f"{test_workspace.id}/transactions/missing.txt",
        b"temporarily unavailable",
        "text/plain",
    )
    attachment = TransactionAttachment(
        transaction_id=test_transactions[0].id,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        filename="missing.txt",
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        size=stored.size,
    )
    session.add(attachment)
    await session.commit()
    attachment_id = attachment.id
    storage_key = attachment.storage_key
    await storage.delete(storage_key)

    data = await backup_service.build_backup_zip(session, test_workspace)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        metadata = json.loads(zf.read("metadata.json"))
        rows = json.loads(zf.read("transaction_attachments.json"))
        assert metadata["omitted_attachment_ids"] == {
            "transaction_attachments": [str(attachment_id)]
        }
        assert rows[0]["id"] == str(attachment_id)
        assert rows[0]["storage_key"] is None
        assert "backup_path" not in rows[0]

    result = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    session.expire_all()
    restored = await session.get(TransactionAttachment, attachment_id)
    assert restored is not None
    assert restored.storage_key == storage_key
    assert result.restored_counts["transaction_attachments"] == 1
    assert any("metadata preserved" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_backup_round_trip_invoices_products_and_files(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
):
    storage = get_storage_provider()
    logo_id = uuid.uuid4()
    logo_data = b"restored invoice logo"
    await storage.upload(
        invoice_logo_service.storage_key(test_workspace.id, logo_id),
        logo_data,
        "image/png",
    )

    product = Product(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Consulting",
    )
    session.add(product)
    await session.flush()
    price = ProductPrice(
        product_id=product.id,
        workspace_id=test_workspace.id,
        currency="USD",
        unit_price=Decimal("125.00"),
    )
    settings = InvoiceSettings(
        workspace_id=test_workspace.id,
        logo_id=logo_id,
        issuer_display_name="Securo Test",
    )
    invoice = Invoice(
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        issue_date=date(2026, 10, 24),
        due_date=date(2026, 11, 23),
        currency="USD",
        total=Decimal("125.00"),
        share_token="must-not-be-restored",
        snapshot={"issuer": {"logo_id": str(logo_id)}},
    )
    session.add_all([price, settings, invoice])
    await session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        workspace_id=test_workspace.id,
        description="Consulting",
        unit_price=Decimal("125.00"),
        total=Decimal("125.00"),
        product_id=product.id,
        price_id=price.id,
    )
    attachment_key = f"{test_workspace.id}/invoices/{invoice.id}/source.pdf"
    attachment_data = b"invoice attachment"
    stored = await storage.upload(attachment_key, attachment_data, "application/pdf")
    attachment = InvoiceAttachment(
        invoice_id=invoice.id,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        filename="source.pdf",
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        size=stored.size,
    )
    session.add_all([line, attachment])
    await session.commit()

    data = await backup_service.build_backup_zip(session, test_workspace)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["entity_counts"]["products"] == 1
        assert metadata["entity_counts"]["invoices"] == 1
        assert metadata["entity_counts"]["invoice_attachments"] == 1
        assert f"invoice_logos/{logo_id}.png" in zf.namelist()

    result = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )

    restored_invoice = (
        await session.execute(select(Invoice).where(Invoice.workspace_id == result.workspace_id))
    ).scalar_one()
    restored_line = (
        await session.execute(
            select(InvoiceLine).where(InvoiceLine.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    restored_product = (
        await session.execute(select(Product).where(Product.workspace_id == result.workspace_id))
    ).scalar_one()
    restored_price = (
        await session.execute(
            select(ProductPrice).where(ProductPrice.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    restored_attachment = (
        await session.execute(
            select(InvoiceAttachment).where(InvoiceAttachment.workspace_id == result.workspace_id)
        )
    ).scalar_one()

    assert restored_invoice.id != invoice.id
    assert restored_invoice.share_token is None
    assert restored_line.invoice_id == restored_invoice.id
    assert restored_line.product_id == restored_product.id
    assert restored_line.price_id == restored_price.id
    restored_key_uuid = restored_attachment.storage_key.rsplit("/", 1)[-1].split("_", 1)[0]
    assert len(restored_key_uuid) == 32
    assert uuid.UUID(hex=restored_key_uuid)
    assert await storage.download(restored_attachment.storage_key) == attachment_data
    assert await invoice_logo_service.read(result.workspace_id, logo_id) == logo_data

    without_logo = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(without_logo, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename != f"invoice_logos/{logo_id}.png":
                target.writestr(info, source.read(info.filename))

    missing_logo_result = await backup_service.restore_backup_bytes(
        session,
        data=without_logo.getvalue(),
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )
    missing_logo_settings = (
        await session.execute(
            select(InvoiceSettings).where(
                InvoiceSettings.workspace_id == missing_logo_result.workspace_id
            )
        )
    ).scalar_one()
    missing_logo_invoice = (
        await session.execute(
            select(Invoice).where(Invoice.workspace_id == missing_logo_result.workspace_id)
        )
    ).scalar_one()
    assert missing_logo_settings.logo_id is None
    assert missing_logo_invoice.snapshot is not None
    assert missing_logo_invoice.snapshot["issuer"]["logo_id"] is None
    assert any("Invoice logo skipped" in warning for warning in missing_logo_result.warnings)

    current_result = await backup_service.restore_backup_bytes(
        session,
        data=without_logo.getvalue(),
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )
    current_settings = (
        await session.execute(
            select(InvoiceSettings).where(InvoiceSettings.workspace_id == test_workspace.id)
        )
    ).scalar_one()
    current_invoice = (
        await session.execute(select(Invoice).where(Invoice.workspace_id == test_workspace.id))
    ).scalar_one()
    assert current_settings is not None
    assert current_settings.logo_id == logo_id
    assert current_invoice.snapshot is not None
    assert current_invoice.snapshot["issuer"]["logo_id"] == str(logo_id)
    assert await invoice_logo_service.read(test_workspace.id, logo_id) == logo_data
    assert not any("Invoice logo skipped" in warning for warning in current_result.warnings)


@pytest.mark.asyncio
async def test_current_restore_preserves_workspace_member_attribution(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    second_user = User(
        email="backup-member@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(second_user)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=second_user.id,
            role="editor",
            invited_by_user_id=test_user.id,
        )
    )
    session.add_all(
        [
            Group(
                workspace_id=test_workspace.id,
                user_id=test_user.id,
                name="Shared name",
            ),
            Group(
                workspace_id=test_workspace.id,
                user_id=second_user.id,
                name="Shared name",
            ),
            RecurringTransaction(
                workspace_id=test_workspace.id,
                user_id=test_user.id,
                account_id=test_account.id,
                description="Shared schedule",
                amount=Decimal("10.00"),
                currency="USD",
                type="debit",
                frequency="monthly",
                start_date=date(2026, 1, 1),
                next_occurrence=date(2026, 11, 1),
            ),
            RecurringTransaction(
                workspace_id=test_workspace.id,
                user_id=second_user.id,
                account_id=test_account.id,
                description="Shared schedule",
                amount=Decimal("10.00"),
                currency="USD",
                type="debit",
                frequency="monthly",
                start_date=date(2026, 1, 1),
                next_occurrence=date(2026, 11, 1),
            ),
        ]
    )
    await session.commit()

    data = await backup_service.build_backup_zip(session, test_workspace)
    await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    group_owners = set(
        (
            await session.execute(
                select(Group.user_id).where(
                    Group.workspace_id == test_workspace.id,
                    Group.name == "Shared name",
                )
            )
        ).scalars()
    )
    recurring_owners = set(
        (
            await session.execute(
                select(RecurringTransaction.user_id).where(
                    RecurringTransaction.workspace_id == test_workspace.id,
                    RecurringTransaction.description == "Shared schedule",
                )
            )
        ).scalars()
    )
    assert group_owners == {test_user.id, second_user.id}
    assert recurring_owners == {test_user.id, second_user.id}

    new_workspace = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )
    restored_groups = (
        (
            await session.execute(
                select(Group).where(Group.workspace_id == new_workspace.workspace_id)
            )
        )
        .scalars()
        .all()
    )
    restored_recurring = (
        (
            await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.workspace_id == new_workspace.workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {group.user_id for group in restored_groups} == {test_user.id}
    assert len({group.name for group in restored_groups}) == 2
    assert {recurring.user_id for recurring in restored_recurring} == {test_user.id}
    assert len({recurring.description for recurring in restored_recurring}) == 2
    assert any("single owner" in warning for warning in new_workspace.warnings)


@pytest.mark.asyncio
async def test_restore_rejects_rule_action_referencing_foreign_workspace_payee(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    rule = Rule(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Unsafe restored rule",
        conditions_op="and",
        conditions=[],
        actions=[],
    )
    foreign_workspace = Workspace(name="Foreign workspace", created_by_user_id=test_user.id)
    session.add_all([rule, foreign_workspace])
    await session.flush()
    foreign_payee = Payee(
        user_id=test_user.id,
        workspace_id=foreign_workspace.id,
        name="Foreign payee",
    )
    session.add(foreign_payee)
    await session.commit()

    valid_data = await backup_service.build_backup_zip(
        session,
        test_workspace,
        content=backup_service.BackupContent.configuration,
    )
    corrupted = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(valid_data)) as source,
        zipfile.ZipFile(corrupted, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "rules.json":
                rows = json.loads(payload)
                rows[0]["actions"] = [{"op": "set_payee", "value": str(foreign_payee.id)}]
                payload = json.dumps(rows).encode()
            target.writestr(info, payload)

    with pytest.raises(HTTPException) as exc_info:
        await backup_service.restore_backup_bytes(
            session,
            data=corrupted.getvalue(),
            target_user_id=test_user.id,
            current_workspace=test_workspace,
            content=backup_service.BackupContent.configuration,
            mode=backup_service.BackupRestoreMode.current_workspace,
            confirmation="RESTORE",
        )

    assert exc_info.value.status_code == 400
    assert "Payee not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_scheduled_backup_failure_rolls_back_before_next_workspace(
    session: AsyncSession,
    test_user: User,
    monkeypatch,
):
    workspace_ids = [uuid.uuid4(), uuid.uuid4()]
    session.add_all(
        [
            Workspace(
                id=workspace_id,
                name=f"Scheduled {index}",
                created_by_user_id=test_user.id,
            )
            for index, workspace_id in enumerate(workspace_ids)
        ]
        + [
            AppSetting(key=backup_service._config_key(workspace_id), value="{}")
            for workspace_id in workspace_ids
        ]
    )
    await session.commit()

    async def fake_get_backup_config(_session, _workspace_id):
        return backup_service.BackupConfig(scheduled_enabled=True)

    attempted_workspace_ids = []

    async def fake_create_stored_backup(_session, workspace, **_kwargs):
        attempted_workspace_ids.append(workspace.id)
        if len(attempted_workspace_ids) == 1:
            raise RuntimeError("first failed")

    monkeypatch.setattr(backup_service, "get_backup_config", fake_get_backup_config)
    monkeypatch.setattr(backup_service, "create_stored_backup", fake_create_stored_backup)
    monkeypatch.setattr(backup_service.get_settings(), "backup_storage_shared", True)

    result = await backup_service.run_scheduled_backups(session)

    assert result == {"created": 1, "skipped": 0, "errors": ["first failed"]}
    assert set(attempted_workspace_ids) == set(workspace_ids)
