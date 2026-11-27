import logging
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

ASYNC_POSTGRES_DRIVER = "postgresql+asyncpg"


def asyncpg_connect_args(database_url: str, force_custom_plans: bool) -> dict[str, object]:
    """Arguments the asyncpg driver takes for this database.

    ``plan_cache_mode`` is a PostgreSQL setting, so it only goes to PostgreSQL
    on asyncpg: the test database is SQLite and another driver would reject an
    argument it does not know. With the setting off nothing is sent and the
    server's own plan_cache_mode stays, which is the way out for an instance
    behind a connection pooler that refuses an unknown startup parameter.
    """
    if not force_custom_plans or not database_url.startswith(ASYNC_POSTGRES_DRIVER):
        return {}
    return {"server_settings": {"plan_cache_mode": "force_custom_plan"}}


def refuses_plan_cache_mode(error: BaseException) -> bool:
    """Whether a connection was refused for the plan_cache_mode startup setting.

    PgBouncer answers "unsupported startup parameter: plan_cache_mode"; a server
    or proxy that does not know the setting answers "unrecognized configuration
    parameter". Any other failure is a real one and is raised as it is.
    """
    message = str(error).lower()
    return "plan_cache_mode" in message and (
        "unsupported startup parameter" in message
        or "unrecognized configuration parameter" in message
    )


def connect_without_refused_plan_setting(engine: AsyncEngine) -> None:
    """Retry a refused connection once without plan_cache_mode, then stop sending it.

    The setting is on by default (issue #584). An instance whose database sits
    behind a pooler that refuses unknown startup parameters (PgBouncer in
    transaction mode) could not connect at all after upgrading. The first
    refusal logs a warning and every later connection of this engine goes
    without the setting, so the API stays up and the server's own
    plan_cache_mode applies.
    """
    state = {"drop": False}

    @event.listens_for(engine.sync_engine, "do_connect")
    def _connect(dialect, conn_rec, cargs, cparams):
        if state["drop"]:
            _drop_plan_setting(cparams)
            return None
        try:
            return dialect.connect(*cargs, **cparams)
        except Exception as error:
            if not refuses_plan_cache_mode(error) or not _drop_plan_setting(cparams):
                raise
            state["drop"] = True
            logger.warning(
                "The database connection refused the plan_cache_mode startup "
                "parameter (%s); connecting without it. Set DB_FORCE_CUSTOM_PLANS=false "
                "to silence this, or allow the parameter in the pooler "
                "(PgBouncer: ignore_startup_parameters).",
                error,
            )
            return dialect.connect(*cargs, **cparams)


def _drop_plan_setting(cparams: dict) -> bool:
    server_settings = cparams.get("server_settings")
    if not server_settings or "plan_cache_mode" not in server_settings:
        return False
    rest = {k: v for k, v in server_settings.items() if k != "plan_cache_mode"}
    if rest:
        cparams["server_settings"] = rest
    else:
        cparams.pop("server_settings")
    return True


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
    connect_args = asyncpg_connect_args(url, settings.db_force_custom_plans)
    engine = create_async_engine(
        url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        connect_args=connect_args,
    )
    if connect_args:
        connect_without_refused_plan_setting(engine)
    return engine


engine = create_app_engine()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncIterator[AsyncSession]:
    from app.core.app_clock import use_timezone

    async with async_session_maker() as session:
        async with use_timezone(session):
            yield session
