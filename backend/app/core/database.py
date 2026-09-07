from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncIterator[AsyncSession]:
    from app.core.app_clock import use_timezone

    async with async_session_maker() as session:
        async with use_timezone(session):
            yield session
