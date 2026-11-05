"""SQLite-only selector/precondition checks, NOT native migration proof."""
from contextlib import aclosing
from inspect import unwrap

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests import conftest
from tests.test_review_concurrency_postgres import schema_sessions as schema_sessions


@pytest.fixture
def selected_modes():
    return []


@pytest_asyncio.fixture
async def postgres_sessions(selected_modes):
    selected_modes.append('metadata')
    yield async_sessionmaker(conftest.engine, info={'fixture_mode': 'metadata'})


@pytest_asyncio.fixture
async def alembic_postgres_sessions(selected_modes):
    selected_modes.append('alembic')
    yield async_sessionmaker(conftest.engine, info={'fixture_mode': 'alembic'})


async def test_actual_selector_resolves_only_selected_async_factory(schema_sessions, selected_modes):
    # Both substitutes are explicitly SQLite: only lazy fixture routing and
    # pytest-asyncio integration are under test, not PostgreSQL or Alembic.
    async with schema_sessions() as session:
        assert session.get_bind().dialect.name == 'sqlite'
        assert await session.scalar(text('SELECT 1')) == 1
        assert selected_modes == [session.info['fixture_mode']]


@pytest.mark.parametrize('url', ['', 'sqlite+aiosqlite:///:memory:', 'postgresql://localhost/test'])
async def test_alembic_preconditions_fail_before_engine_construction(monkeypatch, url):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setenv('POSTGRES_TEST_URL', url)

    def unexpected_engine(*args, **kwargs):
        pytest.fail('Precondition failure must not construct an engine or connect')

    monkeypatch.setattr(conftest, 'create_async_engine', unexpected_engine)
    expected = 'CI must supply POSTGRES_TEST_URL' if not url else 'PostgreSQL\\+asyncpg'
    async with aclosing(unwrap(conftest.alembic_postgres_sessions)()) as fixture:
        with pytest.raises(pytest.fail.Exception, match=expected):
            await anext(fixture)
