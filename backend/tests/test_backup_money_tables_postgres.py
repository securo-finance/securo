"""Backup round trips against real PostgreSQL: foreign keys are only enforced there, so
the order in which restored rows reach the database matters (a loan account points at
the asset that secures it)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app
from tests.test_backup_money_tables import (  # noqa: F401 -- rerun on PostgreSQL
    test_configuration_restore_keeps_the_splits_of_existing_transactions,
    test_round_trip_into_a_new_workspace_keeps_every_amount,
    test_round_trip_into_the_current_workspace_keeps_every_amount,
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
