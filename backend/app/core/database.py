from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# pool_pre_ping validates a pooled connection before handing it to the
# application. Without it, a PostgreSQL failover leaves dead connections in
# the pool and the first request that borrows one fails with
# "connection is closed" instead of transparently reconnecting.
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncIterator[AsyncSession]:
    from app.core.app_clock import use_timezone

    async with async_session_maker() as session:
        async with use_timezone(session):
            yield session
