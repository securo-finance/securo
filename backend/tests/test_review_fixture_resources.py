"""Fake-I/O ownership checks of the actual fixture, NOT native PostgreSQL proof."""
from contextlib import aclosing, asynccontextmanager
from inspect import unwrap
from types import SimpleNamespace

import pytest

from tests import conftest


_SCHEMA = 'postgres_test_0123456789abcdef0123456789abcdef'
_HISTORICAL_SCHEMA = 'postgres_test_historical_unowned'
_FAKE_URL = 'postgresql+asyncpg://fixture.invalid/synthetic_never_connected'


class _FixtureIOFailure(RuntimeError):
    pass


class _FakePostgresEngine:
    """Model only this fixture's I/O boundary; never call a database driver."""

    def __init__(self):
        self.failure = None
        self.schemas = {_HISTORICAL_SCHEMA: ['unowned historical table']}
        self.statements = []
        self.events = []
        self.begin_count = 0
        self.dispose_count = 0

    def execution_options(self, **options):
        self.events.append('scope')
        assert options == {'schema_translate_map': {None: _SCHEMA}}
        if self.failure == 'scope':
            raise _FixtureIOFailure('scope failed')
        return self

    @asynccontextmanager
    async def begin(self):
        self.begin_count += 1
        first = self.begin_count == 1
        if first and self.failure == 'begin':
            raise _FixtureIOFailure('begin failed before CREATE')
        yield self
        if first and self.failure == 'commit':
            # Simulate a lost commit response: the fixture cannot prove whether
            # the CREATE committed. A possible resource must be retained.
            raise _FixtureIOFailure('CREATE commit outcome unknown')

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if sql == f'CREATE SCHEMA "{_SCHEMA}"':
            self.events.append('create')
            if _SCHEMA in self.schemas:
                raise _FixtureIOFailure('CREATE collided with a pre-existing schema')
            if self.failure == 'create':
                raise _FixtureIOFailure('CREATE failed before allocation')
            self.schemas[_SCHEMA] = []
            if self.failure == 'response':
                raise _FixtureIOFailure('CREATE response lost after possible allocation')
        elif sql == f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE':
            self.events.append('drop')
            if self.failure == 'drop':
                raise _FixtureIOFailure('DROP failed')
            self.schemas.pop(_SCHEMA, None)
        else:
            pytest.fail(f'Unexpected fixture SQL: {sql}')

    async def run_sync(self, operation):
        assert operation == conftest.Base.metadata.create_all
        self.events.append('metadata')
        if self.failure == 'metadata':
            raise _FixtureIOFailure('metadata setup failed')
        self.schemas[_SCHEMA].append('owned model tables')

    async def dispose(self):
        self.events.append('dispose')
        self.dispose_count += 1


@pytest.fixture
def fake_postgres_io(monkeypatch):
    engine = _FakePostgresEngine()

    def fake_engine(url):
        assert url == _FAKE_URL
        engine.events.append('engine')
        return engine

    # Install the fake before supplying any non-empty URL. The actual fixture
    # executes, but its engine cannot create a socket or reach PostgreSQL.
    monkeypatch.setattr(conftest, 'create_async_engine', fake_engine)
    monkeypatch.setattr(conftest.uuid, 'uuid4', lambda: SimpleNamespace(hex=_SCHEMA.removeprefix('postgres_test_')))
    monkeypatch.setenv('POSTGRES_TEST_URL', _FAKE_URL)
    return engine


@pytest.mark.parametrize('failure', ['collision', 'begin', 'create', 'response', 'commit', 'scope'])
async def test_unconfirmed_create_never_drops_schema(fake_postgres_io, failure):
    engine = fake_postgres_io
    engine.failure = failure
    if failure == 'collision':
        engine.schemas[_SCHEMA] = ['pre-existing table must survive']
    before = {name: list(tables) for name, tables in engine.schemas.items()}

    async with aclosing(unwrap(conftest.postgres_sessions)()) as fixture:
        with pytest.raises(_FixtureIOFailure):
            await anext(fixture)

    # An ambiguous server-side CREATE is deliberately retained, never adopted.
    expected = dict(before)
    if failure in {'response', 'commit'}:
        expected[_SCHEMA] = []
    assert engine.schemas == expected
    assert not any(sql.startswith('DROP ') for sql in engine.statements)
    assert 'metadata' not in engine.events
    assert engine.dispose_count == 1
    assert engine.events[-1] == 'dispose'


async def test_confirmed_create_yields_then_drops_only_owned_schema(fake_postgres_io):
    engine = fake_postgres_io
    async with aclosing(unwrap(conftest.postgres_sessions)()) as fixture:
        factory = await anext(fixture)
        assert factory.kw['bind'] is engine
        assert factory.kw['expire_on_commit'] is False
        assert engine.schemas[_SCHEMA] == ['owned model tables']
        assert engine.dispose_count == 0
        assert engine.events == ['engine', 'scope', 'create', 'metadata']

    assert engine.statements == [
        f'CREATE SCHEMA "{_SCHEMA}"',
        f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE',
    ]
    assert engine.schemas == {_HISTORICAL_SCHEMA: ['unowned historical table']}
    assert engine.events == ['engine', 'scope', 'create', 'metadata', 'drop', 'dispose']
    assert engine.dispose_count == 1


async def test_confirmed_create_is_cleaned_when_metadata_setup_fails(fake_postgres_io):
    engine = fake_postgres_io
    engine.failure = 'metadata'
    async with aclosing(unwrap(conftest.postgres_sessions)()) as fixture:
        with pytest.raises(_FixtureIOFailure, match='metadata setup failed'):
            await anext(fixture)

    assert engine.schemas == {_HISTORICAL_SCHEMA: ['unowned historical table']}
    assert engine.events == ['engine', 'scope', 'create', 'metadata', 'drop', 'dispose']
    assert engine.dispose_count == 1


async def test_drop_failure_propagates_but_still_disposes_own_engine(fake_postgres_io):
    engine = fake_postgres_io
    engine.failure = 'drop'
    with pytest.raises(_FixtureIOFailure, match='DROP failed'):
        async with aclosing(unwrap(conftest.postgres_sessions)()) as fixture:
            await anext(fixture)

    assert engine.schemas == {
        _HISTORICAL_SCHEMA: ['unowned historical table'],
        _SCHEMA: ['owned model tables'],
    }
    assert engine.events == ['engine', 'scope', 'create', 'metadata', 'drop', 'dispose']
    assert engine.dispose_count == 1
