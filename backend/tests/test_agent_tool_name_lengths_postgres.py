"""Tool lists hold the names discovery returns, on the real schema.

An extra MCP server without an alias is named by its URL, and MCP allows tool
names up to 128 characters. Both are recorded as they are (they route the
call), and rolling the migrations back must not fail on them.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
LONG_SERVER = "https://mcp.example.com/" + "s" * 200 + "/mcp"
LONG_TOOL = "t" * 128


def _alembic(url: str, *args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stderr[-3000:]


async def _admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _execute(url: str, *statements: tuple[str, dict]) -> list:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            out = []
            for sql, params in statements:
                out.append((await conn.execute(text(sql), params)))
            return [r.all() if r.returns_rows else None for r in out]
    finally:
        await engine.dispose()


@pytest.fixture
def database_url():
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    name = f"tool_names_{uuid.uuid4().hex}"
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(url, f'CREATE DATABASE "{name}"'))
    try:
        yield target
    finally:
        asyncio.run(_admin(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_long_names_are_recorded_and_survive_a_rollback(database_url):
    _alembic(database_url, "upgrade", "head")
    user_id, ws_id, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    asyncio.run(_execute(
        database_url,
        ("INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified) "
         "VALUES (:u, 'names@example.com', 'x', true, false, true)", {"u": user_id}),
        ("INSERT INTO workspaces (id, name, kind, created_by_user_id) VALUES (:w, 'W', 'personal', :u)",
         {"w": ws_id, "u": user_id}),
        ("INSERT INTO agents (id, user_id, workspace_id, name) VALUES (:a, :u, :w, 'A')",
         {"a": agent_id, "u": user_id, "w": ws_id}),
        ("INSERT INTO agent_tools (agent_id, server, tool_name, enabled) VALUES (:a, :s, :t, true)",
         {"a": agent_id, "s": LONG_SERVER, "t": LONG_TOOL}),
    ))

    # Back to before tool lists were explicit: the names stay as recorded.
    _alembic(database_url, "downgrade", "115")

    (rows,) = asyncio.run(_execute(database_url, ("SELECT server, tool_name FROM agent_tools", {})))
    assert rows == [(LONG_SERVER, LONG_TOOL)]
