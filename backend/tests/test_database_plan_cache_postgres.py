"""The engine that serves requests hands PostgreSQL the plan cache mode.

The unit tests check the arguments; this one checks them against a real server:
a session opened by the application's engine reports the mode the instance
configured (issue #584).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import create_app_engine


@pytest.fixture
def database_url():
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    return url


async def test_the_app_engine_plans_each_statement_with_its_own_values(database_url):
    """A session opened by the application's engine reports force_custom_plan.

    Without it the session reports the server's own value, which is auto by
    default; a server configured otherwise is skipped here.
    """
    if get_settings().db_force_custom_plans is False:
        pytest.skip("the instance turned custom plans off")

    engine = create_app_engine(database_url)
    try:
        async with engine.connect() as connection:
            mode = await connection.scalar(text("show plan_cache_mode"))
    finally:
        await engine.dispose()

    assert mode == "force_custom_plan"
