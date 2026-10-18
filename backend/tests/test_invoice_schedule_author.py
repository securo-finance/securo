"""Deleting an agreement author preserves the workspace's billing history."""
import uuid
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select

from app.models.invoice import Invoice
from app.models.invoice_attachment import InvoiceAttachment
from app.models.invoice_schedule import InvoiceSchedule
from app.models.user import User
from app.models.workspace import Workspace
from app.services import admin_service, invoice_service, invoice_schedule_service as schedules

AUTHORED_TABLES = ("invoice_schedules", "invoices", "invoice_attachments")


async def _agreement(session):
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
    schedule = await schedules.create_schedule(
        session, workspace.id, author.id,
        {"name": "Retainer", "frequency": "monthly", "start_date": date(2026, 10, 5),
         "lines": [{"description": "Service", "unit_price": "100"}]},
        today=date(2026, 10, 1),
    )
    await session.commit()
    return admin, author, workspace, schedule


async def _migrate_existing(session):
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic/versions/097_invoice_user_fks_set_null.py"
    )
    spec = importlib.util.spec_from_file_location("invoice_user_fks_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = await session.connection()
    schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]

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

    await connection.run_sync(migrate)
    await session.commit()


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
async def test_deleting_a_schedule_author_preserves_and_continues_billing(
    postgres_sessions, migrate_existing
):
    async with postgres_sessions() as session:
        admin, author, workspace, schedule = await _agreement(session)
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


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
async def test_deleting_the_author_of_a_billed_agreement_keeps_what_it_issued(
    postgres_sessions, migrate_existing
):
    async with postgres_sessions() as session:
        admin, author, workspace, schedule = await _agreement(session)
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
