"""A restore that brings back a shared-expense group, against the migrated schema.

The rest of the suite builds its schema from the models, and the models never
declared `uq_groups_user_id_name` — so no SQLite test can see it. Migrations
did create it, in 044, back when a group belonged to a user; 121 drops it.

Why it matters is a restore: every restored row is written for the person
running the restore, in a new workspace. A group they already have ("Casa") in
the workspace the backup came from is the same person and the same name, so
that constraint — scoped to the user, not the workspace — refuses the archive
halfway through the copy. `group_service.create_group` has enforced
workspace-scoped names all along, so the constraint was the database
contradicting the code, and only a real migrated database shows it.
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.group import Group
from app.models.user import User
from app.models.workspace import Workspace
from app.services import schema_versions
from app.services.restore_service import ArchiveContents, restore_workspace

pytestmark = pytest.mark.asyncio

GROUP_NAME = "Casa"


@pytest_asyncio.fixture
async def migrated_sessions(migrated_database_url):
    """Sessions on a database the real migrations built, disposed afterwards."""
    engine = create_async_engine(migrated_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _archive_holding_the_group(group_id: uuid.UUID, user_id: uuid.UUID, workspace_id: uuid.UUID) -> ArchiveContents:
    return ArchiveContents(
        metadata={
            "format_version": "1.1",
            "schema_revision": schema_versions.head_revision(),
            "workspace_name": "Restored",
        },
        workspace={"name": "Restored", "kind": "personal", "default_currency": "USD"},
        entities={
            "groups": [
                {
                    "id": str(group_id),
                    "user_id": str(user_id),
                    "workspace_id": str(workspace_id),
                    "name": GROUP_NAME,
                    "kind": "social",
                    "default_currency": "USD",
                    "icon": "users",
                    "color": "#6B7280",
                    "is_archived": False,
                    "notes": None,
                    "created_at": "2026-05-01T12:00:00+00:00",
                }
            ],
            # The restore reads this key directly; a backup always carries it.
            "transactions": [],
        },
    )


async def _unique_constraints(session) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'groups' AND constraint_type = 'UNIQUE'"
        )
    )
    return {name for (name,) in rows}


async def test_restore_brings_a_group_back_next_to_the_same_name_the_person_already_has(
    migrated_sessions,
):
    async with migrated_sessions() as session:
        assert "uq_groups_user_id_name" not in await _unique_constraints(session), (
            "the compiled migrations still make a group name unique per user; "
            "restoring a backup into a new workspace cannot work with that constraint"
        )

        user = User(
            id=uuid.uuid4(),
            email=f"restore-{uuid.uuid4().hex}@example.com",
            hashed_password="not-used",
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        session.add(user)
        await session.flush()

        source = Workspace(name="Source", kind="personal", created_by_user_id=user.id)
        session.add(source)
        await session.flush()

        # The group the person already had, in the workspace the backup came from.
        original_id = uuid.uuid4()
        session.add(
            Group(
                id=original_id,
                user_id=user.id,
                workspace_id=source.id,
                name=GROUP_NAME,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

        archived_id = uuid.uuid4()
        result = await restore_workspace(
            session,
            user,
            _archive_holding_the_group(archived_id, user.id, source.id),
        )
        await session.commit()

        restored = (
            await session.execute(select(Group).where(Group.workspace_id == result.workspace_id))
        ).scalars().all()

    assert [group.name for group in restored] == [GROUP_NAME]
    assert result.entity_counts["groups"] == 1
    # A fresh key, not the archived one: the copy is a new row, not an alias.
    assert restored[0].id != archived_id
    assert restored[0].id != original_id
