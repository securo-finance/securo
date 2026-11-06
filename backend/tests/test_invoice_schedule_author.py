"""Deleting an agreement author preserves the workspace's billing history."""
import uuid
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text, update

from app.models.invoice import Invoice
from app.models.invoice_attachment import InvoiceAttachment
from app.models.invoice_schedule import InvoiceSchedule
from app.models.payee import Payee, PayeeMapping
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.payee import PayeeCreate, PayeeRead, TaxIdInput
from app.fiscal.registry import TaxIdKind
from app.services import (
    admin_service, invoice_service, invoice_schedule_service as schedules, payee_service,
)

AUTHORED_TABLES = ("invoice_schedules", "invoices", "invoice_attachments", "payees")


async def _client(session, workspace_id, author_id, name="Retained client"):
    return await payee_service.create_payee(
        session, workspace_id, author_id,
        PayeeCreate(name=name, email="client@example.invalid", notes="Keep client notes",
                    tax_ids=[TaxIdInput(kind=TaxIdKind.OTHER, value="client-fiscal-id")]),
    )


async def _agreement(session, with_client=False):
    admin = User(
        id=uuid.uuid4(), email="admin@example.invalid", hashed_password="unused",
        is_active=True, is_superuser=True, is_verified=True,
    )
    author = User(
        id=uuid.uuid4(), email="author@example.invalid", hashed_password="unused",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add_all([admin, author])
    await session.flush()
    workspace = Workspace(
        id=uuid.uuid4(), name="Business", kind="business", created_by_user_id=admin.id,
    )
    session.add(workspace)
    await session.flush()
    client = await _client(session, workspace.id, author.id) if with_client else None
    schedule = await schedules.create_schedule(
        session, workspace.id, author.id,
        {"name": "Retainer", "frequency": "monthly", "start_date": date(2026, 10, 5),
         "lines": [{"description": "Service", "unit_price": "100"}],
         "payee_id": client.id if client else None},
        today=date(2026, 10, 1),
    )
    await session.commit()
    return admin, author, workspace, schedule


def _migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic/versions/103_invoice_user_fks_set_null.py"
    )
    spec = importlib.util.spec_from_file_location("invoice_user_fks_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def _migrate_existing(session):
    migration = _migration()
    connection = await session.connection()
    schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]
    # Alembic ALTER COLUMN emits unqualified DDL, unlike metadata's translated
    # table operations. Keep the migration inside this fixture's schema.
    await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

    def migrate(sync_connection):
        # Start with the shipped FKs, then exercise upgrade, rollback, and
        # re-upgrade while the author's rows already exist.
        with Operations.context(MigrationContext.configure(sync_connection)):
            for operation, expected in (
                (migration.downgrade, None),
                (migration.upgrade, "SET NULL"),
                (migration.downgrade, None),
                (migration.upgrade, "SET NULL"),
            ):
                operation()
                for table in AUTHORED_TABLES:
                    fks = inspect(sync_connection).get_foreign_keys(table, schema=schema)
                    author_fk = next(fk for fk in fks if fk["constrained_columns"] == ["user_id"])
                    assert author_fk["options"].get("ondelete") == expected, table
                user_column = next(
                    column for column in inspect(sync_connection).get_columns("payees", schema=schema)
                    if column["name"] == "user_id"
                )
                assert user_column["nullable"] is (expected == "SET NULL")

    await connection.run_sync(migrate)
    await session.commit()


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
@pytest.mark.parametrize("with_client", [False, True], ids=["no-client", "author-client"])
async def test_deleting_a_schedule_author_preserves_and_continues_billing(
    postgres_sessions, migrate_existing, with_client,
):
    async with postgres_sessions() as session:
        admin, author, workspace, schedule = await _agreement(session, with_client)
        author_id = author.id
        if migrate_existing:
            await _migrate_existing(session)

        assert await admin_service.delete_user(session, author_id, admin.id)
        await session.refresh(schedule)
        assert await session.get(User, author_id) is None
        assert schedule.user_id is None
        assert schedule.status == "active"
        [invoice] = await schedules.generate_due(session, schedule, today=date(2026, 10, 5))
        await session.commit()
        assert invoice.user_id is None
        assert invoice.workspace_id == workspace.id
        assert invoice.status == "open"
        assert invoice.sequence == 1 and invoice.total == 100
        assert schedule.next_sequence == 2
        assert invoice.payee_id == schedule.payee_id
        if with_client:
            await _assert_client_survives(session, schedule.payee_id, workspace.id)


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
@pytest.mark.parametrize("with_client", [False, True], ids=["no-client", "author-client"])
async def test_deleting_the_author_of_a_billed_agreement_keeps_what_it_issued(
    postgres_sessions, migrate_existing, with_client,
):
    async with postgres_sessions() as session:
        admin, author, workspace, schedule = await _agreement(session, with_client)
        author_id = author.id
        [issued] = await schedules.generate_due(session, schedule, today=date(2026, 10, 5))
        await session.commit()
        loaded = await invoice_service.get_invoice(session, issued.id, workspace.id)
        assert loaded is not None
        await schedules.after_generation(session, loaded, workspace)
        invoice_id = issued.id
        filed = (
            await session.execute(
                select(InvoiceAttachment).where(InvoiceAttachment.invoice_id == invoice_id)
            )
        ).scalar_one()
        assert issued.user_id == author_id and filed.user_id == author_id
        if migrate_existing:
            await _migrate_existing(session)

        assert await admin_service.delete_user(session, author_id, admin.id)
        session.expunge_all()
        assert await session.get(User, author_id) is None
        survivor = await session.get(InvoiceSchedule, schedule.id)
        assert survivor is not None and survivor.user_id is None
        invoice = await session.get(Invoice, invoice_id)
        assert invoice is not None and invoice.user_id is None
        assert invoice.schedule_id == schedule.id and invoice.status == "open"
        attachment = await session.get(InvoiceAttachment, filed.id)
        assert attachment is not None and attachment.user_id is None
        assert invoice.payee_id == survivor.payee_id
        if with_client:
            await _assert_client_survives(session, survivor.payee_id, workspace.id)


async def _assert_client_survives(session, payee_id, workspace_id):
    client = await payee_service.get_payee(session, payee_id, workspace_id)
    assert client is not None
    await session.refresh(client, attribute_names=["user_id", "tax_ids"])
    assert client.user_id is None and client.workspace_id == workspace_id
    payload = PayeeRead.model_validate(client, from_attributes=True).model_dump()
    assert payload["user_id"] is None and payload["id"] == payee_id
    assert payload["name"] == "Retained client" and payload["notes"] == "Keep client notes"
    assert payload["email"] == "client@example.invalid"
    assert payload["source"] == "manual"
    assert payload["tax_ids"] == [{"kind": "other", "value": "client-fiscal-id"}]


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
async def test_deleting_a_client_creator_keeps_other_authors_invoices_and_unrelated_cleanup(
    postgres_sessions, migrate_existing,
):
    async with postgres_sessions() as session:
        admin, author, workspace, _ = await _agreement(session)
        client = await _client(session, workspace.id, author.id)
        unused = await _client(session, workspace.id, author.id, name="Unreferenced client")
        unused_id, client_id, author_id = unused.id, client.id, author.id
        other_mapping = PayeeMapping(
            user_id=admin.id, workspace_id=workspace.id, target_id=client_id,
        )
        session.add(other_mapping)
        # No schedule references this client, and the surviving invoice was
        # written by somebody else. Client retention follows the reference,
        # not the creator of the billing record.
        invoice = await invoice_service.create_invoice(
            session, workspace.id, admin.id,
            {"payee_id": client_id, "total": Decimal("100"), "issue_date": date(2026, 10, 5)},
        )
        await session.commit()
        if migrate_existing:
            await _migrate_existing(session)

        assert await admin_service.delete_user(session, author_id, admin.id)
        await session.refresh(invoice)
        assert invoice.payee_id == client_id and invoice.user_id == admin.id
        assert await session.get(Payee, unused_id, populate_existing=True) is None
        assert await session.scalar(select(PayeeMapping.id).where(
            PayeeMapping.user_id == author_id,
        )) is None
        assert await session.get(PayeeMapping, other_mapping.id) is not None
        await _assert_client_survives(session, client_id, workspace.id)


async def test_downgrade_refuses_to_invent_a_retained_client_creator(postgres_sessions):
    async with postgres_sessions() as session:
        admin, author, workspace, schedule = await _agreement(session, with_client=True)
        assert await admin_service.delete_user(session, author.id, admin.id)
        migration = _migration()
        connection = await session.connection()
        schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

        def refuse_without_changing_schema(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                with pytest.raises(RuntimeError, match="Reassign payees with no creator"):
                    migration.downgrade()
                for table in AUTHORED_TABLES:
                    foreign_keys = inspect(sync_connection).get_foreign_keys(table, schema=schema)
                    author_fk = next(fk for fk in foreign_keys if fk["constrained_columns"] == ["user_id"])
                    assert author_fk["options"].get("ondelete") == "SET NULL"
                user_column = next(
                    column for column in inspect(sync_connection).get_columns("payees", schema=schema)
                    if column["name"] == "user_id"
                )
                assert user_column["nullable"] is True

        await connection.run_sync(refuse_without_changing_schema)
        await _assert_client_survives(session, schedule.payee_id, workspace.id)
        # An operator explicitly supplies a valid synthetic custodian before
        # restoring the old contract. The migration never makes this choice.
        await session.execute(update(Payee).where(Payee.id == schedule.payee_id).values(user_id=admin.id))
        await session.commit()
        await _migrate_existing(session)


async def test_generation_refreshes_an_author_deleted_after_the_schedule_was_loaded(
    postgres_sessions,
):
    async with postgres_sessions() as worker:
        admin, author, workspace, schedule = await _agreement(worker)
        author_id = author.id
        async with postgres_sessions() as deleting:
            assert await admin_service.delete_user(deleting, author_id, admin.id)

        # The worker already loaded the old author. PostgreSQL cleared the FK
        # in another session, so the post-lock refresh must reload that field.
        assert schedule.user_id == author_id
        [invoice] = await schedules.generate_due(worker, schedule, today=date(2026, 10, 5))
        await worker.commit()
        assert schedule.user_id is None
        assert invoice.user_id is None and invoice.workspace_id == workspace.id
        assert invoice.sequence == 1 and schedule.next_sequence == 2
