"""Backup → restore, round trip, through the HTTP API.

The point of these tests is the promise behind the feature: the archive a user
downloads is one they can actually restore. So the main test restores into a
fresh workspace and compares what came back — counts, sums in `Decimal`, the
attachment bytes, the transferred ids a naive copy would leave dangling.
"""
import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.providers as providers_module
from app.core.config import get_settings
from app.services import restore_service
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.budget import Budget
from app.models.category import Category
from app.models.collection import Collection, collection_accounts
from app.models.credit_card_bill import CreditCardBill
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeTaxId
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceTaxId

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point the storage singleton at a directory of this test's own."""
    monkeypatch.setattr(get_settings(), "storage_local_path", str(tmp_path))
    monkeypatch.setattr(providers_module, "_storage_provider", None)
    yield tmp_path
    providers_module._storage_provider = None


async def _restore(client: AsyncClient, headers: dict, content: bytes, password: str | None = None):
    data = {"password": password} if password is not None else None
    return await client.post(
        "/api/export/restore",
        headers=headers,
        files={"file": ("backup.zip", content, "application/zip")},
        data=data,
    )


def _rewrite_archive(
    content: bytes,
    *,
    drop_prefix: str | None = None,
    metadata: dict | None = None,
    duplicate_row: str | None = None,
) -> bytes:
    """Rebuild a plain zip, removing blobs or patching metadata at will."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            if drop_prefix and name.startswith(drop_prefix):
                continue
            payload = source.read(name)
            if name == "metadata.json" and metadata is not None:
                parsed = json.loads(payload)
                parsed.update(metadata)
                payload = json.dumps(parsed).encode()
            if duplicate_row and name == f"{duplicate_row}.json":
                rows = json.loads(payload)
                if rows:
                    rows.append({**rows[0], "id": str(uuid.uuid4())})
                payload = json.dumps(rows).encode()
            target.writestr(name, payload)
    return out.getvalue()


def _patch_entity_rows(
    content: bytes,
    entity: str,
    *,
    patch_first: dict | None = None,
    copy_first_with: dict | None = None,
) -> bytes:
    """Rebuild a plain zip with one entity file's rows edited."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == f"{entity}.json":
                rows = json.loads(payload)
                if patch_first and rows:
                    rows[0].update(patch_first)
                if copy_first_with is not None and rows:
                    rows.append({**rows[0], "id": str(uuid.uuid4()), **copy_first_with})
                payload = json.dumps(rows).encode()
            target.writestr(name, payload)
    return out.getvalue()


def _stored_files(root) -> list:
    return sorted(path for path in root.rglob("*") if path.is_file())


async def _seed_rich_workspace(session: AsyncSession, client: AsyncClient, headers: dict, user, account, transactions, categories):
    """Everything format 1.1 carries, so the round trip has something to check."""
    workspace_id = account.workspace_id

    payee = Payee(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace_id, name="Padaria do Zé")
    session.add(payee)
    await session.flush()
    session.add(PayeeTaxId(id=uuid.uuid4(), payee_id=payee.id, workspace_id=workspace_id, kind="cpf", value="123"))
    transactions[0].payee_id = payee.id

    # A transfer: two legs pointing at each other, and an installment series
    # id, both plain UUIDs with no foreign key behind them.
    first, second = transactions[0], transactions[1]
    first.transfer_pair_id = second.id
    second.transfer_pair_id = first.id
    second.installment_series_id = uuid.uuid4()

    group = Group(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace_id, name="Casa", kind="social")
    session.add(group)
    await session.flush()
    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=workspace_id, name="Eu", is_self=True)
    ana = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=workspace_id, name="Ana")
    session.add_all([me, ana])
    await session.flush()
    session.add(
        TransactionSplit(
            id=uuid.uuid4(),
            transaction_id=first.id,
            workspace_id=workspace_id,
            group_member_id=ana.id,
            share_amount=Decimal("10.00"),
            share_type="exact",
        )
    )
    session.add(
        GroupSettlement(
            id=uuid.uuid4(),
            group_id=group.id,
            workspace_id=workspace_id,
            from_member_id=ana.id,
            to_member_id=me.id,
            amount=Decimal("5.00"),
            currency="BRL",
            date=date.today(),
        )
    )

    collection = Collection(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace_id, name="Dia a dia")
    session.add(collection)
    await session.flush()
    await session.execute(
        collection_accounts.insert().values(collection_id=collection.id, account_id=account.id)
    )

    asset_group = AssetGroup(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace_id, name="Ações")
    session.add(asset_group)
    await session.flush()
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id,
        name="PETR4",
        type="investment",
        currency="BRL",
        group_id=asset_group.id,
        purchase_price=Decimal("100.00"),
    )
    session.add(asset)
    session.add(
        Goal(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace_id,
            name="Reserva",
            target_amount=Decimal("10000.00"),
            tracking_type="manual",
        )
    )
    session.add(
        Budget(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace_id,
            category_id=categories[0].id,
            amount=Decimal("300.00"),
            month=date.today().replace(day=1),
        )
    )
    session.add(
        CreditCardBill(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace_id,
            account_id=account.id,
            external_id="bill-1",
            due_date=date.today(),
            total_amount=Decimal("250.00"),
            currency="BRL",
        )
    )
    log = ImportLog(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id,
        account_id=account.id,
        entity="transactions",
        filename="extrato.ofx",
        format="ofx",
        transaction_count=3,
        total_credit=Decimal("100.00"),
        total_debit=Decimal("50.00"),
    )
    session.add(log)
    await session.flush()
    transactions[2].import_id = log.id

    # The investment ledger: the buy/sell entries the holding's units and cost
    # basis are replayed from. One of them points at the import that wrote it.
    session.add(
        AssetTransaction(
            id=uuid.uuid4(),
            asset_id=asset.id,
            workspace_id=workspace_id,
            kind="buy",
            quantity=Decimal("10.000000"),
            price=Decimal("100.000000"),
            fee=Decimal("2.50"),
            date=date.today(),
            source="import",
            import_id=log.id,
        )
    )

    session.add(
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace_id,
            account_id=account.id,
            category_id=categories[0].id,
            description="Aluguel",
            amount=Decimal("1200.00"),
            currency="BRL",
            type="debit",
            frequency="monthly",
            start_date=date.today(),
            next_occurrence=date.today(),
        )
    )
    session.add(WorkspaceTaxId(id=uuid.uuid4(), workspace_id=workspace_id, kind="cnpj", value="99887766000155"))

    workspace = await session.get(Workspace, workspace_id)
    assert workspace is not None
    workspace.default_currency = "BRL"
    workspace.locale = "pt-BR"
    workspace.timezone = "America/Sao_Paulo"
    workspace.legal_name = "Securo Testes Ltda"
    workspace.icon = "briefcase"
    workspace.color = "#123456"
    await session.commit()

    upload = await client.post(
        f"/api/transactions/{first.id}/attachments",
        headers=headers,
        files={"file": ("recibo.png", TINY_PNG, "image/png")},
    )
    assert upload.status_code == 201, upload.text
    return {"workspace_id": workspace_id, "transaction_id": first.id}


@pytest.mark.asyncio
async def test_round_trip_carries_every_entity_and_its_ids(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_user: User,
    test_account: Account,
    test_transactions: list[Transaction],
    test_categories: list[Category],
    test_rules: list[Rule],
    storage,
):
    seeded = await _seed_rich_workspace(
        session, client, auth_headers, test_user, test_account, test_transactions, test_categories
    )
    original_workspace = seeded["workspace_id"]
    original = await session.get(Workspace, original_workspace)
    assert original is not None
    original_name = original.name
    original_sum = sum((txn.amount for txn in test_transactions), Decimal("0.00"))

    exported = await client.get("/api/export/backup", headers=auth_headers)
    assert exported.status_code == 200

    restored = await _restore(client, auth_headers, exported.content)
    assert restored.status_code == 201, restored.text
    body = restored.json()
    new_workspace = uuid.UUID(body["workspace_id"])
    assert new_workspace != original_workspace
    assert body["attachments_restored"] == 1
    assert body["attachments_missing"] == 0

    counts = body["entity_counts"]
    assert counts["transactions"] == len(test_transactions)
    assert counts["payees"] == 1
    assert counts["rules"] == len(test_rules)
    assert counts["transaction_splits"] == 1
    assert counts["group_settlements"] == 1
    assert counts["collections"] == 1
    assert counts["transaction_attachments"] == 1
    assert counts["asset_transactions"] == 1

    # The workspace itself: settings carried, caller is its owner.
    workspace = await session.get(Workspace, new_workspace)
    assert workspace is not None
    assert workspace.name == original_name
    assert workspace.default_currency == "BRL"
    assert workspace.locale == "pt-BR"
    assert workspace.timezone == "America/Sao_Paulo"
    assert workspace.legal_name == "Securo Testes Ltda"
    assert workspace.created_by_user_id == test_user.id
    membership = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == new_workspace,
                WorkspaceMember.user_id == test_user.id,
            )
        )
    ).scalar_one()
    assert membership.role == "owner"

    # Money comes back exact, and the ledger is whole.
    restored_transactions = (
        await session.execute(select(Transaction).where(Transaction.workspace_id == new_workspace))
    ).scalars().all()
    assert len(restored_transactions) == len(test_transactions)
    assert sum((txn.amount for txn in restored_transactions), Decimal("0.00")) == original_sum
    assert {txn.user_id for txn in restored_transactions} == {test_user.id}
    assert all(txn.account_id for txn in restored_transactions)

    # A transfer pair still points at itself, on fresh ids.
    by_description = {txn.description: txn for txn in restored_transactions}
    uber = by_description["UBER TRIP"]
    ifood = by_description["IFOOD RESTAURANTE"]
    assert uber.transfer_pair_id == ifood.id
    assert ifood.transfer_pair_id == uber.id
    assert ifood.installment_series_id is not None

    # A rule's category id lives inside JSON with no foreign key: it has to be
    # rewritten, or the rule silently stops matching anything.
    restored_rule = (
        await session.execute(
            select(Rule).where(Rule.workspace_id == new_workspace, Rule.name == "UBER rule")
        )
    ).scalar_one()
    action_category = uuid.UUID(restored_rule.actions[0]["value"])
    category = await session.get(Category, action_category)
    assert category is not None and category.workspace_id == new_workspace

    # Payee and its tax id came along, and the transaction points at the copy.
    payee = (
        await session.execute(select(Payee).where(Payee.workspace_id == new_workspace))
    ).scalar_one()
    assert payee.name == "Padaria do Zé"
    assert uber.payee_id == payee.id
    tax_id = (
        await session.execute(select(PayeeTaxId).where(PayeeTaxId.workspace_id == new_workspace))
    ).scalar_one()
    assert tax_id.payee_id == payee.id

    # The investment ledger survives, re-pointed at the restored holding and
    # the restored import rather than at rows that stayed behind.
    restored_asset = (
        await session.execute(select(Asset).where(Asset.workspace_id == new_workspace))
    ).scalar_one()
    ledger_entry = (
        await session.execute(
            select(AssetTransaction).where(AssetTransaction.workspace_id == new_workspace)
        )
    ).scalar_one()
    assert ledger_entry.asset_id == restored_asset.id
    assert ledger_entry.kind == "buy"
    assert ledger_entry.quantity == Decimal("10.000000")
    restored_log = await session.get(ImportLog, ledger_entry.import_id)
    assert restored_log is not None and restored_log.workspace_id == new_workspace

    # The shared-expense cluster: split, member and settlement all on new ids.
    split = (
        await session.execute(select(TransactionSplit).where(TransactionSplit.workspace_id == new_workspace))
    ).scalar_one()
    assert split.transaction_id == uber.id
    member = await session.get(GroupMember, split.group_member_id)
    assert member is not None
    assert member.workspace_id == new_workspace and member.name == "Ana"
    settlement = (
        await session.execute(
            select(GroupSettlement).where(GroupSettlement.workspace_id == new_workspace)
        )
    ).scalar_one()
    assert settlement.group_id == member.group_id
    assert settlement.from_member_id == member.id

    # Collection membership survives with both sides remapped.
    restored_collection = (
        await session.execute(select(Collection).where(Collection.workspace_id == new_workspace))
    ).scalar_one()
    linked = (
        await session.execute(
            select(collection_accounts).where(collection_accounts.c.collection_id == restored_collection.id)
        )
    ).mappings().all()
    assert len(linked) == 1
    assert linked[0]["account_id"] in {txn.account_id for txn in restored_transactions}

    # The attachment: fresh row, fresh storage key, same bytes.
    attachment = (
        await session.execute(
            select(TransactionAttachment).where(TransactionAttachment.workspace_id == new_workspace)
        )
    ).scalar_one()
    assert attachment.transaction_id == uber.id
    assert attachment.size == len(TINY_PNG)
    assert attachment.storage_key.startswith(f"{new_workspace}/")
    downloaded = await client.get(
        f"/api/transactions/{attachment.transaction_id}/attachments/{attachment.id}",
        headers={**auth_headers, "X-Workspace-Id": str(new_workspace)},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == TINY_PNG

    # And nothing landed in the workspace the archive came from.
    original_count = (
        await session.execute(
            select(Transaction).where(Transaction.workspace_id == original_workspace)
        )
    ).scalars().all()
    assert len(original_count) == len(test_transactions)


@pytest.mark.asyncio
async def test_restore_twice_creates_two_workspaces(
    client: AsyncClient, auth_headers, test_account: Account
):
    exported = await client.get("/api/export/backup", headers=auth_headers)
    first = await _restore(client, auth_headers, exported.content)
    second = await _restore(client, auth_headers, exported.content)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["workspace_id"] != second.json()["workspace_id"]
    assert first.json()["entity_counts"] == second.json()["entity_counts"]


@pytest.mark.asyncio
async def test_restore_of_an_encrypted_backup(
    client: AsyncClient, auth_headers, test_account: Account
):
    password = "correct horse battery"
    exported = await client.post("/api/export/backup", json={"password": password}, headers=auth_headers)
    assert exported.status_code == 200

    without_password = await _restore(client, auth_headers, exported.content)
    assert without_password.status_code == 400

    restored = await _restore(client, auth_headers, exported.content, password)
    assert restored.status_code == 201, restored.text
    assert restored.json()["entity_counts"]["accounts"] == 1


@pytest.mark.asyncio
async def test_restore_rejects_a_bad_password(client: AsyncClient, auth_headers, test_account: Account):
    exported = await client.post(
        "/api/export/backup", json={"password": "correct horse battery"}, headers=auth_headers
    )
    response = await _restore(client, auth_headers, exported.content, "wrong horse battery")
    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_rejects_a_file_without_metadata(client: AsyncClient, auth_headers):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("transactions.json", "[]")
    response = await _restore(client, auth_headers, buf.getvalue())
    assert response.status_code == 400
    assert "metadata" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_rejects_an_upload_that_is_not_a_zip(client: AsyncClient, auth_headers):
    """A PNG or a truncated download must be a 400, not an unhandled error.

    `pyzipper` raises its own `BadZipFile`, a different class from the stdlib
    one; the archive reader has to name both to answer this quietly.
    """
    response = await _restore(client, auth_headers, b"\x89PNG\r\n\x1a\nnot a zip")
    assert response.status_code == 400
    assert "archive" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_rejects_a_newer_format(client: AsyncClient, auth_headers):
    exported = await client.get("/api/export/backup", headers=auth_headers)
    content = _rewrite_archive(exported.content, metadata={"format_version": "1.2"})
    response = await _restore(client, auth_headers, content)
    assert response.status_code == 400
    assert "format" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_rejects_a_newer_schema(client: AsyncClient, auth_headers):
    exported = await client.get("/api/export/backup", headers=auth_headers)
    content = _rewrite_archive(exported.content, metadata={"schema_revision": "999"})
    response = await _restore(client, auth_headers, content)
    assert response.status_code == 400
    assert "schema" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_skips_an_attachment_missing_from_the_archive(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_user: User,
    test_account: Account,
    test_transactions: list[Transaction],
    storage,
):
    upload = await client.post(
        f"/api/transactions/{test_transactions[0].id}/attachments",
        headers=auth_headers,
        files={"file": ("recibo.png", TINY_PNG, "image/png")},
    )
    assert upload.status_code == 201, upload.text

    exported = await client.get("/api/export/backup", headers=auth_headers)
    without_blob = _rewrite_archive(exported.content, drop_prefix="attachments/")

    restored = await _restore(client, auth_headers, without_blob)
    assert restored.status_code == 201, restored.text
    body = restored.json()
    assert body["attachments_restored"] == 0
    assert body["attachments_missing"] == 1
    # The transaction beside it still came back.
    assert body["entity_counts"]["transactions"] == len(test_transactions)


@pytest.mark.asyncio
async def test_restore_unauthenticated(client: AsyncClient):
    response = await client.post(
        "/api/export/restore", files={"file": ("backup.zip", b"", "application/zip")}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_restore_refuses_an_archive_with_two_identical_bills(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_workspace,
    test_user: User,
    test_account: Account,
):
    """Every restored row belongs to the caller, so two identical bills collide.

    A member's bill and another member's identical one cannot both survive the
    copy; the restore says which bill and stops before writing anything.
    """
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Aluguel",
            amount=Decimal("1200.00"),
            currency="BRL",
            type="debit",
            frequency="monthly",
            start_date=date(2026, 1, 5),
            next_occurrence=date(2026, 1, 5),
        )
    )
    await session.commit()

    before = (await session.execute(select(Workspace))).scalars().all()
    exported = await client.get("/api/export/backup", headers=auth_headers)
    content = _rewrite_archive(exported.content, duplicate_row="recurring_transactions")
    response = await _restore(client, auth_headers, content)

    assert response.status_code == 400
    assert "Aluguel" in response.json()["detail"]
    after = (await session.execute(select(Workspace))).scalars().all()
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_restore_refuses_an_archive_with_two_budgets_for_the_same_category(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_workspace,
    test_user: User,
    test_categories: list[Category],
):
    """A budget is unique per person, category, month and type.

    Two members budgeting the same category for the same month in the source
    workspace become the same person's duplicates once the copy writes every
    row for whoever restores. Refusing the file names the category and month
    instead of letting the unique constraint surface as a 500 halfway through.
    """
    month = date.today().replace(day=1)
    member = User(
        id=uuid.uuid4(),
        email="member@example.com",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
    )
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=test_workspace.id, user_id=member.id, role="editor"))
    session.add_all(
        Budget(
            id=uuid.uuid4(),
            user_id=owner_id,
            workspace_id=test_workspace.id,
            category_id=test_categories[0].id,
            amount=Decimal("500.00"),
            month=month,
        )
        for owner_id in (test_user.id, member.id)
    )
    await session.commit()

    before = (await session.execute(select(Workspace))).scalars().all()
    exported = await client.get("/api/export/backup", headers=auth_headers)
    response = await _restore(client, auth_headers, exported.content)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert test_categories[0].name in detail
    assert month.isoformat() in detail
    after = (await session.execute(select(Workspace))).scalars().all()
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_restore_writes_nothing_when_the_file_is_refused(
    client: AsyncClient, session: AsyncSession, auth_headers
):
    before = (await session.execute(select(Workspace))).scalars().all()
    exported = await client.get("/api/export/backup", headers=auth_headers)
    content = _rewrite_archive(exported.content, metadata={"format_version": "9.9"})
    response = await _restore(client, auth_headers, content)
    assert response.status_code == 400
    after = (await session.execute(select(Workspace))).scalars().all()
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_restore_nulls_a_receiver_credit_that_lives_in_another_workspace(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_user: User,
    test_account: Account,
    test_transactions: list[Transaction],
    test_categories: list[Category],
    storage,
):
    """A settlement's mirror credit may sit in a workspace the archive does not carry.

    `settlement_service` credits the receiver's first account in any workspace
    they belong to, so this pointer can name a transaction outside the export.
    That link is optional: the settlement still comes back, with the pointer
    null, instead of the whole restore being refused.
    """
    await _seed_rich_workspace(
        session, client, auth_headers, test_user, test_account, test_transactions, test_categories
    )
    exported = await client.get("/api/export/backup", headers=auth_headers)
    outside = str(uuid.uuid4())
    content = _patch_entity_rows(
        exported.content, "group_settlements", patch_first={"receiver_transaction_id": outside}
    )

    restored = await _restore(client, auth_headers, content)
    assert restored.status_code == 201, restored.text

    new_workspace = uuid.UUID(restored.json()["workspace_id"])
    settlement = (
        await session.execute(
            select(GroupSettlement).where(GroupSettlement.workspace_id == new_workspace)
        )
    ).scalar_one()
    assert settlement.receiver_transaction_id is None
    assert settlement.amount == Decimal("5.00")


@pytest.mark.asyncio
async def test_restore_refuses_an_archive_that_expands_past_the_entry_limit(
    client: AsyncClient, auth_headers, monkeypatch
):
    """A small upload must not be able to make the worker allocate a huge entry."""
    monkeypatch.setattr(restore_service, "_MAX_ENTRY_BYTES", 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps({"pad": "x" * 4096}))

    response = await _restore(client, auth_headers, buf.getvalue())

    assert response.status_code == 400
    assert "larger than" in response.json()["detail"]


@pytest.mark.asyncio
async def test_restore_refuses_a_directory_named_entry_that_expands_past_the_limit(
    client: AsyncClient, auth_headers, monkeypatch
):
    """A name ending in `/` is reported as a directory, not forbidden from holding bytes.

    `open_archive` reads every `attachments/...` entry, directory flag or not,
    so the size limit has to count those entries too.
    """
    monkeypatch.setattr(restore_service, "_MAX_ENTRY_BYTES", 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", "{}")
        zf.writestr("attachments/payload/", "x" * 4096)

    response = await _restore(client, auth_headers, buf.getvalue())

    assert response.status_code == 400
    assert "larger than" in response.json()["detail"]


@pytest.mark.asyncio
async def test_restore_refuses_an_archive_that_expands_past_the_total_limit(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(restore_service, "_MAX_TOTAL_BYTES", 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps({"pad": "x" * 4096}))

    response = await _restore(client, auth_headers, buf.getvalue())

    assert response.status_code == 400
    assert "expands to more than" in response.json()["detail"]


@pytest.mark.asyncio
async def test_restore_removes_uploaded_objects_when_it_fails(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers,
    test_account: Account,
    test_transactions: list[Transaction],
    storage,
):
    """A failure after the first upload must not leave an orphan in storage.

    The rewritten archive carries the real attachment and a second row that
    points at a transaction the archive does not hold. The first blob is
    uploaded, the second row aborts the copy, and the object already written
    would be unreachable: the row that pointed at it never commits.
    """
    upload = await client.post(
        f"/api/transactions/{test_transactions[0].id}/attachments",
        headers=auth_headers,
        files={"file": ("recibo.png", TINY_PNG, "image/png")},
    )
    assert upload.status_code == 201, upload.text

    exported = await client.get("/api/export/backup", headers=auth_headers)
    broken = _patch_entity_rows(
        exported.content,
        "transaction_attachments",
        copy_first_with={"transaction_id": str(uuid.uuid4())},
    )
    before = _stored_files(storage)

    response = await _restore(client, auth_headers, broken)

    assert response.status_code == 400
    assert _stored_files(storage) == before
