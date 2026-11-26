import os
import subprocess
import sys
import textwrap

import pytest

# conftest imports app.core.database, which builds the engine from
# DATABASE_URL at import time. Run the application engine in a fresh
# interpreter so the test observes production code, not a private engine.
RECONNECT_SCRIPT = textwrap.dedent(
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.database import engine


    async def main() -> None:
        url = engine.url.render_as_string(hide_password=False)
        killer = create_async_engine(url, pool_size=1, max_overflow=0)

        async with engine.connect() as connection:
            backend_pid = await connection.scalar(text("select pg_backend_pid()"))

        async with killer.connect() as connection:
            await connection.execute(
                text("select pg_terminate_backend(:pid)"), {"pid": backend_pid}
            )

        async with engine.connect() as connection:
            assert await connection.scalar(text("select 1")) == 1

        await engine.dispose()
        await killer.dispose()


    asyncio.run(main())
    """
)


POOL_LIMITS_SCRIPT = textwrap.dedent(
    """
    from sqlalchemy.pool import QueuePool

    from app.core.database import engine

    pool = engine.pool
    assert isinstance(pool, QueuePool)
    print(pool.size(), pool._max_overflow)
    """
)


def test_pool_recovers_from_a_dropped_connection():
    """A pooled connection killed server-side must not fail the next request.

    This is what happens during a PostgreSQL failover: the primary goes away
    and every connection the pool still holds is dead. ``pool_pre_ping``
    detects that before handing the connection out and transparently opens a
    new one.
    """
    url = os.environ.get("POSTGRES_TEST_URL")

    if not url:
        pytest.skip("isolated PostgreSQL not configured")

    result = subprocess.run(
        [sys.executable, "-c", RECONNECT_SCRIPT],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_pool_limits_come_from_the_environment():
    """DB_POOL_SIZE and DB_MAX_OVERFLOW reach the engine that serves requests.

    The API opens its engine at import time, so a changed limit only shows up
    in a fresh interpreter. With 40 people at once the default pool ran out and
    requests failed instead of queueing (issue #1901); raising the two settings
    is how an instance takes more load.
    """
    result = subprocess.run(
        [sys.executable, "-c", POOL_LIMITS_SCRIPT],
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+asyncpg://securo:secret@localhost:5432/securo",
            "DB_POOL_SIZE": "7",
            "DB_MAX_OVERFLOW": "3",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["7", "3"]
