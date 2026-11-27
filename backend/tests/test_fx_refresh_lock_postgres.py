"""The day lock the manual FX refresh takes, against real PostgreSQL.

SQLite runs one write at a time, so a test there cannot tell whether the lock
exists. Here the first refresh is held open inside its own transaction — with
its day lock taken, before it commits — and a second refresh of the same day
has to wait for it. Removing the lock turns this test red, and the two
refreshes would spend two provider calls for one day (issue #636).
"""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.app_clock import app_today
from app.services import fx_rate_service


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


def _hold_the_fetch(monkeypatch) -> tuple[asyncio.Event, asyncio.Event, list[date]]:
    """Pause the refresh after it took the day lock, before it commits.

    The provider call runs inside the refresh's transaction, so gating it
    keeps the transaction — and the lock — open until the test releases it.
    """
    reached = asyncio.Event()
    release = asyncio.Event()
    calls: list[date] = []

    async def held(provider, target):
        calls.append(target)
        reached.set()
        await release.wait()
        return {"BRL": Decimal("5.00")}

    monkeypatch.setattr(fx_rate_service, "_fetch", held)
    return reached, release, calls


@pytest.mark.asyncio
async def test_a_second_refresh_of_the_same_day_waits_for_the_first(
    postgres_sessions,
    clean_db,
    monkeypatch,
):
    """Two administrators refreshing at once spend one provider call, not two.

    While the first refresh is fetching, a second one of the same day waits on
    the day lock instead of counting no stored rate and fetching too. A short
    `lock_timeout` makes the wait fail instead of hanging the suite; once the
    first commits, a later refresh finds the stored day and skips the provider.
    """
    today = app_today()
    reached, release, calls = _hold_the_fetch(monkeypatch)

    async with postgres_sessions() as first, postgres_sessions() as second:
        task = asyncio.create_task(fx_rate_service.sync_rates_for_refresh(first, today))
        await asyncio.wait_for(reached.wait(), timeout=30)

        await second.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(DBAPIError) as exc:
            # Bounded on purpose: without the day lock the second refresh
            # reaches the paused fetch and waits there forever, and
            # `lock_timeout` does not cover that wait.
            await asyncio.wait_for(
                fx_rate_service.sync_rates_for_refresh(second, today),
                timeout=15,
            )
        assert "lock timeout" in str(exc.value).lower()

        release.set()
        count, skipped = await asyncio.wait_for(task, timeout=30)
        assert (count, skipped) == (1, False)

    async with postgres_sessions() as later:
        count, skipped = await fx_rate_service.sync_rates_for_refresh(later, today)
        assert (count, skipped) == (1, True)

    assert calls == [today]
