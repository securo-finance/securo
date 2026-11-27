"""A pooler that refuses plan_cache_mode must not take the API down.

PgBouncer in transaction mode refuses unknown startup parameters, and the
setting is on by default since issue #584. The engine retries once without it,
warns, and keeps connecting without it.
"""
import logging

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import connect_without_refused_plan_setting, refuses_plan_cache_mode


class ProtocolViolationError(Exception):
    """Stands in for asyncpg's error of the same name."""


def _engine_behind_refusing_pooler(attempts: list[dict]):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # What asyncpg_connect_args hands the driver on PostgreSQL.
    @event.listens_for(engine.sync_engine, "do_connect")
    def _with_plan_setting(dialect, conn_rec, cargs, cparams):
        cparams["server_settings"] = {"plan_cache_mode": "force_custom_plan"}

    real_connect = engine.dialect.connect

    def pooler_connect(*cargs, **cparams):
        attempts.append(dict(cparams))
        if "server_settings" in cparams:
            raise ProtocolViolationError("unsupported startup parameter: plan_cache_mode")
        return real_connect(*cargs, **cparams)

    engine.dialect.connect = pooler_connect  # type: ignore[method-assign]
    connect_without_refused_plan_setting(engine)
    return engine


@pytest.mark.asyncio
async def test_connects_without_the_setting_when_the_pooler_refuses_it(caplog):
    attempts: list[dict] = []
    engine = _engine_behind_refusing_pooler(attempts)
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.database"):
            async with engine.connect() as connection:
                assert await connection.scalar(text("select 1")) == 1
        assert [("server_settings" in a) for a in attempts] == [True, False]
        assert "plan_cache_mode" in caplog.text

        # Later connections go straight without it, with no new warning.
        caplog.clear()
        await engine.dispose()
        async with engine.connect() as connection:
            assert await connection.scalar(text("select 1")) == 1
        assert [("server_settings" in a) for a in attempts] == [True, False, False]
        assert caplog.text == ""
    finally:
        await engine.dispose()


def test_only_a_refusal_of_the_setting_triggers_the_retry():
    assert refuses_plan_cache_mode(
        ProtocolViolationError("unsupported startup parameter: plan_cache_mode")
    )
    assert refuses_plan_cache_mode(
        Exception('unrecognized configuration parameter "plan_cache_mode"')
    )
    assert not refuses_plan_cache_mode(Exception("password authentication failed"))
    assert not refuses_plan_cache_mode(Exception("unsupported startup parameter: search_path"))
