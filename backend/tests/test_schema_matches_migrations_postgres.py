"""The migrations build the schema the models describe (ADR 0010).

Every other test builds its schema from the models (``Base.metadata.create_all``),
so a model change without its migration passes the whole suite and only fails
on someone's upgrade. This applies every migration to an empty PostgreSQL
database and compares the result with the models, the way ``alembic
revision --autogenerate`` would.

Drift that predates this test is frozen in ``schema_drift_baseline.txt``. Any
difference not in that file fails: write the migration, or fix the model.
A line in that file that no longer shows up also fails, so fixed drift is
removed from the list and the list only shrinks.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import Base
from app.models import *  # noqa: F401,F403
from app.agents.models import *  # noqa: F401,F403

pytestmark = pytest.mark.asyncio

BACKEND = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "schema_drift_baseline.txt"


def _columns(columns) -> str:
    return ",".join(getattr(column, "name", str(column)) for column in columns)


def _key(diff) -> str:
    """A stable, readable line for one autogenerate difference."""
    if isinstance(diff, list):  # column modifications come grouped in a list
        return "; ".join(_key(item) for item in diff)
    op = diff[0]
    if op.startswith("modify_"):
        _, _, table, column = diff[:4]
        return f"{op} {table}.{column}"
    if op in ("add_table", "remove_table"):
        return f"{op} {diff[1].name}"
    if op in ("add_column", "remove_column"):
        return f"{op} {diff[2]}.{diff[3].name}"
    if op in ("add_index", "remove_index"):
        index: Index = diff[1]
        table = index.table.name if index.table is not None else "?"
        return f"{op} {table} {index.name}"
    if op in ("add_constraint", "remove_constraint"):
        constraint = diff[1]
        kind = "unique" if isinstance(constraint, UniqueConstraint) else type(constraint).__name__
        return f"{op} {constraint.table.name} {kind}({_columns(constraint.columns)})"
    if op in ("add_fk", "remove_fk"):
        fk: ForeignKeyConstraint = diff[1]
        return f"{op} {fk.table.name} ({_columns(fk.columns)}) -> {fk.referred_table.name}"
    return repr(diff)


def _baseline() -> set[str]:
    lines = BASELINE.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


@pytest.fixture
def migrated_database_url():
    """An empty database, upgraded to head by the real migrations, dropped afterwards."""
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    name = f"schema_check_{uuid.uuid4().hex}"
    target = make_url(url).set(database=name).render_as_string(hide_password=False)

    async def admin(statement: str) -> None:
        engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": target},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr[-4000:]}"
        yield target
    finally:
        asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def test_migrations_build_the_schema_the_models_describe(migrated_database_url):
    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn, opts={"compare_type": True}),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()

    found = {_key(diff) for diff in diffs}
    baseline = _baseline()
    new = sorted(found - baseline)
    fixed = sorted(baseline - found)
    assert not new, (
        "The migrations and the models disagree. Write a migration for the model change "
        "(or fix the model), do not add these to the baseline:\n" + "\n".join(new)
    )
    assert not fixed, (
        "Drift in schema_drift_baseline.txt is gone, remove these lines:\n" + "\n".join(fixed)
    )
