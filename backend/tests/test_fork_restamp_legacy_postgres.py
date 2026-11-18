from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fork_restamp_legacy.py"
_spec = importlib.util.spec_from_file_location("fork_restamp_legacy", SCRIPT)
assert _spec and _spec.loader
fork_restamp_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fork_restamp_legacy)

UPSTREAM_ONLY = {f"{n:03d}" for n in range(1, 119)} | {"fork_001", "fork_002", "fork_003"}


async def _execute(url: str, *statements: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _read_versions(url: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT version_num FROM alembic_version ORDER BY 1"))
            return [row[0] for row in rows]
    finally:
        await engine.dispose()


@pytest.fixture
def database_url():
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    name = f"fork_restamp_{uuid.uuid4().hex}"
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_execute(url, f'CREATE DATABASE "{name}"'))
    try:
        yield target
    finally:
        asyncio.run(_execute(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _stamp(url: str, *versions: str) -> None:
    values = ", ".join(f"('{v}')" for v in versions)
    asyncio.run(
        _execute(
            url,
            "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)",
            f"INSERT INTO alembic_version VALUES {values}",
        )
    )


def _versions(url: str) -> list[str]:
    return asyncio.run(_read_versions(url))


def test_a_legacy_stamp_becomes_the_upstream_and_fork_heads(database_url):
    _stamp(database_url, "121")

    restamped = asyncio.run(fork_restamp_legacy.restamp(database_url, UPSTREAM_ONLY))

    assert restamped == ["118", "fork_003"]
    assert _versions(database_url) == ["118", "fork_003"]


def test_a_second_run_changes_nothing(database_url):
    _stamp(database_url, "120")
    asyncio.run(fork_restamp_legacy.restamp(database_url, UPSTREAM_ONLY))

    again = asyncio.run(fork_restamp_legacy.restamp(database_url, UPSTREAM_ONLY))

    assert again is None
    assert _versions(database_url) == ["118", "fork_002"]


def test_an_id_upstream_now_owns_is_left_alone(database_url):
    _stamp(database_url, "119")

    restamped = asyncio.run(fork_restamp_legacy.restamp(database_url, UPSTREAM_ONLY | {"119"}))

    assert restamped is None
    assert _versions(database_url) == ["119"]
