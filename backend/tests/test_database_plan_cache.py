"""The plan cache mode the API asks PostgreSQL for, and where it must not go.

The report endpoints were planned once per statement and reused for every call.
With a generic plan PostgreSQL sizes the plan for an average call, and on an
instance with a large history a report was about twice as slow as the same
statement planned with the values of that call (issue #584).
"""
from app.core.database import asyncpg_connect_args


def test_asyncpg_connects_with_the_configured_plan_cache_mode():
    """The mode reaches an asyncpg connection as a PostgreSQL server setting."""

    args = asyncpg_connect_args(
        "postgresql+asyncpg://securo:secret@db:5432/securo", "force_custom_plan"
    )

    assert args == {"server_settings": {"plan_cache_mode": "force_custom_plan"}}


def test_an_empty_mode_sends_no_server_setting():
    """Empty keeps whatever the server is configured with, which is what an
    instance behind a connection pooler needs: a pooler may refuse a startup
    parameter it does not know."""

    assert asyncpg_connect_args("postgresql+asyncpg://securo:secret@db:5432/securo", "") == {}


def test_other_databases_never_receive_a_postgres_setting():
    """The setting belongs to PostgreSQL. The test database is SQLite, and a
    PostgreSQL URL on another driver would hand the argument to that driver."""

    assert asyncpg_connect_args("sqlite+aiosqlite:///:memory:", "force_custom_plan") == {}
    assert asyncpg_connect_args("sqlite://", "force_custom_plan") == {}
    assert asyncpg_connect_args("postgresql+psycopg://securo:secret@db:5432/securo", "force_custom_plan") == {}
