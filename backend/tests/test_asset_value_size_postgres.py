"""Valuations of a billion and more against real PostgreSQL (issue #641): SQLite
stores any number, so only here does a column too narrow for them fail."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app
from tests.test_asset_value_size import (  # noqa: F401 -- rerun on PostgreSQL
    test_a_goal_on_a_flat_worth_billions_shows_its_value_and_history,
    test_a_valuation_of_a_billion_or_more_is_recorded,
    test_a_valuation_past_the_column_is_a_422_without_sql,
    test_an_asset_created_past_the_column_is_a_422_without_sql,
    test_an_asset_created_with_a_current_value_of_a_billion_is_kept,
)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
