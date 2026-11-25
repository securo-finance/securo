from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

ASYNC_POSTGRES_DRIVER = "postgresql+asyncpg"


def asyncpg_connect_args(database_url: str, plan_cache_mode: str) -> dict[str, object]:
    """Arguments the asyncpg driver takes for this database.

    ``plan_cache_mode`` is a PostgreSQL setting, so it only goes to PostgreSQL
    on asyncpg: the test database is SQLite and another driver would reject an
    argument it does not know. An empty mode sends nothing and leaves the
    server's own value, which is the way out for an instance behind a
    connection pooler that refuses an unknown startup parameter.
    """
    if not plan_cache_mode or not database_url.startswith(ASYNC_POSTGRES_DRIVER):
        return {}
    return {"server_settings": {"plan_cache_mode": plan_cache_mode}}


def create_app_engine(database_url: str | None = None) -> AsyncEngine:
    """The engine that serves requests, with the settings of the instance.

    pool_pre_ping validates a pooled connection before handing it to the
    application. Without it, a PostgreSQL failover leaves dead connections in
    the pool and the first request that borrows one fails with
    "connection is closed" instead of transparently reconnecting.

    pool_size and max_overflow come from the instance settings, so a busy
    instance can raise them together with the database's max_connections
    (issue #1901).
    """
    url = database_url or settings.database_url
    return create_async_engine(
        url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        connect_args=asyncpg_connect_args(url, settings.db_plan_cache_mode),
    )


engine = create_app_engine()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncIterator[AsyncSession]:
    from app.core.app_clock import use_timezone

    async with async_session_maker() as session:
        async with use_timezone(session):
            yield session
