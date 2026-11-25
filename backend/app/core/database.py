from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# pool_pre_ping validates a pooled connection before handing it to the
# application. Without it, a PostgreSQL failover leaves dead connections in
# the pool and the first request that borrows one fails with
# "connection is closed" instead of transparently reconnecting.
#
# pool_size and max_overflow come from the instance settings, so a busy
# instance can raise them together with the database's max_connections
# (issue #1901).
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncIterator[AsyncSession]:
    from app.core.app_clock import use_timezone

    async with async_session_maker() as session:
        async with use_timezone(session):
            yield session
