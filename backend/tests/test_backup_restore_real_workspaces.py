"""Backup and restore of workspaces shaped like real ones (#283).

Two things every lived-in workspace has broke restore with a 400: a payee
made through the API (its self-mapping shares the payee's id) and a split
into a group that lives in another workspace (#1305).
"""

import io
import json
import uuid
import zipfile
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.group import Group, GroupMember
from app.models.payee import Payee, PayeeMapping
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import backup_service


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_STORAGE_PATH", tmp_path)
    yield tmp_path


async def _shared_group(
    session: AsyncSession, owner: User, *, member_of: bool
) -> tuple[GroupMember, GroupMember]:
    """A group in another workspace: its owner's self-member and a partner."""
    home = Workspace(
        name="Casa", kind="shared", created_by_user_id=owner.id, default_currency="BRL"
    )
    session.add(home)
    await session.flush()
    if member_of:
        session.add(WorkspaceMember(workspace_id=home.id, user_id=owner.id, role="owner"))
    group = Group(user_id=owner.id, workspace_id=home.id, name="Casal", default_currency="BRL")
    session.add(group)
    await session.flush()
    me = GroupMember(
        group_id=group.id,
        workspace_id=home.id,
        name="Me",
        is_self=True,
        linked_user_id=owner.id,
    )
    partner = GroupMember(group_id=group.id, workspace_id=home.id, name="Bruno")
    session.add_all([me, partner])
    await session.flush()
    return me, partner


async def _summary(client: AsyncClient, headers: dict, workspace_id) -> dict:
    resp = await client.get(
        "/api/dashboard/summary", headers={**headers, "X-Workspace-Id": str(workspace_id)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {
        key: body[key]
        for key in (
            "total_balance",
            "total_balance_primary",
            "monthly_income_primary",
            "monthly_expenses_primary",
        )
    }


async def _count(session: AsyncSession, model, workspace_id) -> int:
    count = await session.scalar(
        select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
    )
    return count or 0


@pytest.mark.asyncio
async def test_round_trip_with_api_payee_and_shared_group_split(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    payee_resp = await client.post(
        "/api/payees",
        json={"name": "Padaria"},
        headers={**auth_headers, "X-Workspace-Id": str(test_workspace.id)},
    )
    assert payee_resp.status_code in (200, 201), payee_resp.text
    payee_id = uuid.UUID(payee_resp.json()["id"])

    # A manual account: restore detaches bank connections, which changes
    # where a connected account's balance comes from.
    test_account.connection_id = None
    test_account.external_id = None
    await session.commit()

    unsplit = await _summary(client, auth_headers, test_workspace.id)
    me, partner = await _shared_group(session, test_user, member_of=True)
    member_ids = {me.id, partner.id}
    ifood = next(t for t in test_transactions if t.description == "IFOOD RESTAURANTE")
    session.add_all(
        TransactionSplit(
            transaction_id=ifood.id,
            workspace_id=test_workspace.id,
            group_member_id=member_id,
            share_amount=Decimal("22.50"),
            share_type="equal",
        )
        for member_id in member_ids
    )
    await session.commit()

    source_id = test_workspace.id
    before = await _summary(client, auth_headers, source_id)
    # The dinner is shared, so the month's expenses really depend on the
    # split surviving the restore.
    assert before["monthly_expenses_primary"] < unsplit["monthly_expenses_primary"]
    tx_count = await _count(session, Transaction, source_id)
    account_count = await _count(session, Account, source_id)

    run_resp = await client.post("/api/backups/run", json={"content": "both"}, headers=auth_headers)
    assert run_resp.status_code == 200, run_resp.text
    backup_id = run_resp.json()["id"]

    restore_resp = await client.post(
        f"/api/backups/{backup_id}/restore",
        json={"content": "both", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restore_resp.status_code == 200, restore_resp.text
    restored_id = uuid.UUID(restore_resp.json()["workspace_id"])

    session.expire_all()
    assert await _count(session, Transaction, restored_id) == tx_count
    assert await _count(session, Account, restored_id) == account_count
    # The group lives in another workspace, so the copy comes back without
    # its split (#344): the dinner counts in full there, and once in the group.
    assert await _summary(client, auth_headers, restored_id) == unsplit
    assert any("another workspace" in w for w in restore_resp.json()["warnings"])

    restored_payee = (
        await session.execute(
            select(Payee).where(Payee.workspace_id == restored_id, Payee.name == "Padaria")
        )
    ).scalar_one()
    assert restored_payee.id != payee_id
    mapping = (
        await session.execute(select(PayeeMapping).where(PayeeMapping.workspace_id == restored_id))
    ).scalar_one()
    # The self-mapping still shares its payee's id, which is how a merge
    # tells a payee's own mapping from the ones folded into it.
    assert mapping.id == restored_payee.id == mapping.target_id

    assert await _count(session, TransactionSplit, restored_id) == 0

    current_resp = await client.post(
        f"/api/backups/{backup_id}/restore",
        json={"content": "both", "mode": "current_workspace", "confirmation": "RESTORE"},
        headers=auth_headers,
    )
    assert current_resp.status_code == 200, current_resp.text
    session.expire_all()
    assert await _count(session, Transaction, source_id) == tx_count
    assert await _summary(client, auth_headers, source_id) == before
    # Back in its own workspace the transaction keeps its id, so its split
    # into the shared group comes back with it.
    current_splits = (
        await session.execute(
            select(TransactionSplit).where(TransactionSplit.workspace_id == source_id)
        )
    ).scalars().all()
    assert {split.group_member_id for split in current_splits} == member_ids
    assert {split.share_amount for split in current_splits} == {Decimal("22.50")}
    current_mapping = (
        await session.execute(select(PayeeMapping).where(PayeeMapping.workspace_id == source_id))
    ).scalar_one()
    assert current_mapping.id == current_mapping.target_id == payee_id


@pytest.mark.asyncio
async def test_restore_skips_split_into_a_group_the_user_cannot_reach(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    stranger = User(
        email="stranger@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    session.add(stranger)
    await session.flush()
    _, foreign_member = await _shared_group(session, stranger, member_of=False)
    await session.commit()

    source = test_transactions[0]
    archive = backup_service.build_backup_archive(
        {
            "metadata.json": {
                "format_version": backup_service.FORMAT_VERSION,
                "content": "data",
                "workspace_id": str(test_workspace.id),
                "workspace_name": test_workspace.name,
                "entity_counts": {"accounts": 1, "transactions": 1, "transaction_splits": 1},
                "warnings": [],
            },
            "accounts.json": [backup_service._serialize_model(test_account)],
            "transactions.json": [backup_service._serialize_model(source)],
            "transaction_splits.json": [
                {
                    "id": str(uuid.uuid4()),
                    "transaction_id": str(source.id),
                    "workspace_id": str(test_workspace.id),
                    "group_member_id": str(foreign_member.id),
                    "share_amount": "10.00",
                    "share_type": "exact",
                }
            ],
        }
    )

    result = await backup_service.restore_backup_bytes(
        session,
        data=archive,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )

    assert result.restored_counts["transactions"] == 1
    assert result.restored_counts["transaction_splits"] == 0
    assert any("split" in warning for warning in result.warnings)
    assert await _count(session, TransactionSplit, result.workspace_id) == 0


def _split_archive(workspace: Workspace, account, transactions, splits: list[tuple]) -> bytes:
    """An archive with these transactions and (transaction, member, amount) splits."""
    return backup_service.build_backup_archive(
        {
            "metadata.json": {
                "format_version": backup_service.FORMAT_VERSION,
                "content": "data",
                "workspace_id": str(workspace.id),
                "workspace_name": workspace.name,
                "entity_counts": {
                    "accounts": 1,
                    "transactions": len(transactions),
                    "transaction_splits": len(splits),
                },
                "warnings": [],
            },
            "accounts.json": [backup_service._serialize_model(account)],
            "transactions.json": [backup_service._serialize_model(t) for t in transactions],
            "transaction_splits.json": [
                {
                    "id": str(uuid.uuid4()),
                    "transaction_id": str(transaction.id),
                    "workspace_id": str(workspace.id),
                    "group_member_id": str(member_id),
                    "share_amount": amount,
                    "share_type": "exact",
                }
                for transaction, member_id, amount in splits
            ],
        }
    )


@pytest.mark.asyncio
async def test_restore_drops_every_share_of_a_transaction_with_a_lost_member(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    stranger = User(
        email="stranger@example.com", hashed_password="x", is_active=True, is_verified=True
    )
    session.add(stranger)
    await session.flush()
    me, partner = await _shared_group(session, test_user, member_of=True)
    _, foreign_member = await _shared_group(session, stranger, member_of=False)
    await session.commit()

    broken, whole = test_transactions[0], test_transactions[1]
    archive = _split_archive(
        test_workspace,
        test_account,
        [broken, whole],
        [
            # 40/60 where the 60 went to a group the user lost: keeping the
            # 40 alone would no longer add up to the transaction.
            (broken, me.id, "40.00"),
            (broken, foreign_member.id, "60.00"),
            (whole, me.id, "5.00"),
            (whole, partner.id, "5.00"),
        ],
    )

    result = await backup_service.restore_backup_bytes(
        session,
        data=archive,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    assert result.restored_counts["transactions"] == 2
    assert result.restored_counts["transaction_splits"] == 2
    assert any("split" in warning for warning in result.warnings)
    restored = (
        await session.execute(
            select(TransactionSplit).where(TransactionSplit.workspace_id == result.workspace_id)
        )
    ).scalars().all()
    assert {split.group_member_id for split in restored} == {me.id, partner.id}
    assert {split.share_amount for split in restored} == {Decimal("5.00")}
    assert len({split.transaction_id for split in restored}) == 1


async def _expenses(client: AsyncClient, headers: dict, workspace_id, day) -> str:
    month_start = day.replace(day=1)
    resp = await client.get(
        "/api/reports/income-expenses",
        params={
            "start_date": month_start.isoformat(),
            "end_date": day.isoformat(),
            "interval": "monthly",
        },
        headers={**headers, "X-Workspace-Id": str(workspace_id)},
    )
    assert resp.status_code == 200, resp.text
    return json.dumps(resp.json(), sort_keys=True)


@pytest.mark.asyncio
async def test_restored_copies_leave_the_shared_group_and_its_members_alone(
    client: AsyncClient,
    auth_headers,
    backup_dir,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    """#344: Ana's backup has a dinner split with Bruno in "Casal", a group of
    the shared "Casa" workspace. Restoring it into a new workspace, by Ana or
    by Bruno, makes a copy of the dinner; its split would add the dinner to
    the group again and move both members' balances and reports."""
    from app.services.balance_service import compute_balances

    bruno = User(email="bruno@example.com", hashed_password="x", is_active=True, is_verified=True)
    session.add(bruno)
    await session.flush()
    bruno_home = Workspace(name="Bruno", created_by_user_id=bruno.id, default_currency="BRL")
    session.add(bruno_home)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=bruno_home.id, user_id=bruno.id, role="owner"))
    ana_member, bruno_member = await _shared_group(session, test_user, member_of=True)
    bruno_member.linked_user_id = bruno.id
    casa_id, group_id = ana_member.workspace_id, ana_member.group_id
    session.add(WorkspaceMember(workspace_id=casa_id, user_id=bruno.id, role="editor"))
    dinner = next(t for t in test_transactions if t.description == "IFOOD RESTAURANTE")
    session.add_all(
        TransactionSplit(
            transaction_id=dinner.id,
            workspace_id=test_workspace.id,
            group_member_id=member.id,
            share_amount=Decimal("22.50"),
            share_type="equal",
        )
        for member in (ana_member, bruno_member)
    )
    await session.commit()
    ana_id, bruno_id, ana_home_id = test_user.id, bruno.id, test_workspace.id
    bruno_home_id = bruno_home.id
    member_ids = [ana_member.id, bruno_member.id]
    bruno_member_id, day = bruno_member.id, dinner.date

    async def group_state() -> tuple:
        session.expire_all()
        balances = [
            await compute_balances(session, group_id, casa_id, user_id)
            for user_id in (ana_id, bruno_id)
        ]
        splits = await session.scalar(
            select(func.count())
            .select_from(TransactionSplit)
            .where(TransactionSplit.group_member_id.in_(member_ids))
        )
        return json.dumps(balances, sort_keys=True, default=str), splits

    group_before = await group_state()
    assert group_before[1] == 2
    report_before = await _expenses(client, auth_headers, ana_home_id, day)

    run_resp = await client.post("/api/backups/run", json={"content": "both"}, headers=auth_headers)
    assert run_resp.status_code == 200, run_resp.text
    _, archive = await backup_service.get_stored_backup_bytes(
        session, ana_home_id, run_resp.json()["id"]
    )

    for restorer_id, home_id in ((ana_id, ana_home_id), (bruno_id, bruno_home_id)):
        home = await session.get(Workspace, home_id)
        assert home is not None
        result = await backup_service.restore_backup_bytes(
            session,
            data=archive,
            target_user_id=restorer_id,
            current_workspace=home,
            content=backup_service.BackupContent.both,
            mode=backup_service.BackupRestoreMode.new_workspace,
        )
        assert await group_state() == group_before
        assert await _expenses(client, auth_headers, ana_home_id, day) == report_before
        assert result.restored_counts["transaction_splits"] == 0
        assert any("another workspace" in warning for warning in result.warnings)

    # Bruno never becomes the payer of a copy of Ana's dinner in the group.
    payers = (
        await session.execute(
            select(Transaction.user_id)
            .join(TransactionSplit, TransactionSplit.transaction_id == Transaction.id)
            .where(TransactionSplit.group_member_id == bruno_member_id)
        )
    ).scalars().all()
    assert payers == [ana_id]


@pytest.mark.asyncio
async def test_restore_into_a_managed_workspace_keeps_the_split_visibility_rule(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account,
    test_transactions,
):
    """A manager restoring into a workspace they are not a member of cannot
    bring in splits of their own group from elsewhere through an unlinked
    self-member: split_service and the group pages hide that group there."""
    client_owner = User(
        email="client@example.com", hashed_password="x", is_active=True, is_verified=True
    )
    session.add(client_owner)
    await session.flush()
    managed = Workspace(
        name="Cliente", kind="business", created_by_user_id=client_owner.id,
        default_currency="BRL",
    )
    session.add(managed)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=managed.id, user_id=client_owner.id, role="owner"))
    me, partner = await _shared_group(session, test_user, member_of=False)
    me.linked_user_id = None
    await session.commit()

    source = test_transactions[0]
    archive = _split_archive(
        test_workspace,
        test_account,
        [source],
        [(source, me.id, "5.00"), (source, partner.id, "5.00")],
    )
    # Restoring into a workspace keeps the archive's ids, so the rows it was
    # taken from go first, as when the archive came from another instance.
    for transaction in test_transactions:
        await session.delete(transaction)
    await session.flush()
    await session.delete(test_account)
    await session.commit()

    result = await backup_service.restore_backup_bytes(
        session,
        data=archive,
        target_user_id=test_user.id,
        current_workspace=managed,
        content=backup_service.BackupContent.data,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    assert result.restored_counts["transactions"] == 1
    assert result.restored_counts["transaction_splits"] == 0
    assert any("split" in warning for warning in result.warnings)
    assert await _count(session, TransactionSplit, managed.id) == 0


def _archive(files: dict[str, list[dict]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, rows in files.items():
            zf.writestr(f"{name}.json", json.dumps(rows))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_id_map_keeps_a_payee_and_its_self_mapping_on_one_id():
    payee_id = str(uuid.uuid4())
    merged_id = str(uuid.uuid4())
    zf = _archive(
        {
            "payees": [{"id": payee_id}],
            "payee_mappings": [
                {"id": payee_id, "target_id": payee_id},
                {"id": merged_id, "target_id": payee_id},
            ],
        }
    )

    id_map = backup_service._build_id_map(zf, ["payees", "payee_mappings"], remap=True)

    new_payee_id = id_map.by_table["payees"][payee_id]
    assert new_payee_id != payee_id
    assert id_map.by_table["payee_mapping"][payee_id] == new_payee_id
    assert id_map.by_table["payee_mapping"][merged_id] not in (merged_id, new_payee_id)


def test_id_map_still_refuses_an_id_reused_by_unrelated_tables():
    shared_id = str(uuid.uuid4())
    zf = _archive({"payees": [{"id": shared_id}], "categories": [{"id": shared_id}]})

    with pytest.raises(ValueError, match="reused by"):
        backup_service._build_id_map(zf, ["categories", "payees"], remap=True)
