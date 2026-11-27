"""The plan each statement is planned with, and where the setting must not go.

PostgreSQL planned the report statements once and reused the plan for every
call. A plan sized for an average call ran a report about twice as slow on an
instance with a large history (issue #584), so the application engine asks for
a plan built with the values of the call that runs it.
"""
from app.core.database import asyncpg_connect_args


def test_asyncpg_connects_with_custom_plans():
    """The setting reaches an asyncpg connection as a PostgreSQL server setting."""

    args = asyncpg_connect_args("postgresql+asyncpg://securo:secret@db:5432/securo", True)

    assert args == {"server_settings": {"plan_cache_mode": "force_custom_plan"}}


def test_turned_off_sends_no_server_setting():
    """Off keeps whatever the server is configured with, which is what an
    instance behind a connection pooler needs: a pooler may refuse a startup
    parameter it does not know."""

    assert asyncpg_connect_args("postgresql+asyncpg://securo:secret@db:5432/securo", False) == {}


def test_other_databases_never_receive_a_postgres_setting():
    """The setting belongs to PostgreSQL. The test database is SQLite, and a
    PostgreSQL URL on another driver would hand the argument to that driver."""

    assert asyncpg_connect_args("sqlite+aiosqlite:///:memory:", True) == {}
    assert asyncpg_connect_args("sqlite://", True) == {}
    assert asyncpg_connect_args("postgresql+psycopg://securo:secret@db:5432/securo", True) == {}
