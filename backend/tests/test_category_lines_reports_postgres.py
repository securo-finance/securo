"""The category-parts report scenario on real PostgreSQL.

Same tests as `test_category_lines_reports.py`; only the database changes,
since the line join, the numeric ratio and `to_char` behave differently on
SQLite.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app
from tests.test_category_lines_reports import *  # noqa: F403 -- rerun every test here
from tests.test_category_lines_reports import scene  # noqa: F401 -- explicit for linters


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
