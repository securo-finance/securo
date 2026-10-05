"""Deleting an agreement author preserves the workspace's billing history."""
import uuid
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.models.user import User
from app.models.workspace import Workspace
from app.services import admin_service, invoice_schedule_service as schedules


@pytest.mark.parametrize("migrate_existing", [False, True], ids=["model", "migration"])
async def test_deleting_a_schedule_author_preserves_and_continues_billing(
    postgres_sessions, migrate_existing
):
    async with postgres_sessions() as session:
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
        author_id = author.id
        await session.commit()

        if migrate_existing:
            path = (
                Path(__file__).resolve().parent.parent
                / "alembic/versions/097_invoice_schedule_author_set_null.py"
            )
            spec = importlib.util.spec_from_file_location("schedule_author_migration", path)
            assert spec is not None and spec.loader is not None
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            connection = await session.connection()
            schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]

            def migrate(sync_connection):
                # Start with the shipped FK, then exercise upgrade, rollback,
                # and re-upgrade while the author's agreement already exists.
                with Operations.context(MigrationContext.configure(sync_connection)):
                    for operation, expected in (
                        (migration.downgrade, None),
                        (migration.upgrade, "SET NULL"),
                        (migration.downgrade, None),
                        (migration.upgrade, "SET NULL"),
                    ):
                        operation()
                        fks = inspect(sync_connection).get_foreign_keys("invoice_schedules", schema=schema)
                        author_fk = next(fk for fk in fks if fk["constrained_columns"] == ["user_id"])
                        assert author_fk["options"].get("ondelete") == expected

            await connection.run_sync(migrate)
            await session.commit()

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
