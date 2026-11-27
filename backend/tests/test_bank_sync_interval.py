"""Exercise automatic eligibility against the real SQL selection, with no bank IO."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.bank_connection import BankConnection
from app.tasks.sync_tasks import _sync_all


def test_default_interval_is_four_hours(monkeypatch):
    monkeypatch.delenv("BANK_SYNC_MIN_INTERVAL_HOURS", raising=False)
    assert Settings(_env_file=None, _secrets_dir=None).bank_sync_min_interval_hours == 4


@pytest.mark.parametrize("value", ["6", "0.5"])
def test_interval_from_environment(monkeypatch, value):
    monkeypatch.setenv("BANK_SYNC_MIN_INTERVAL_HOURS", value)
    assert Settings(_env_file=None, _secrets_dir=None).bank_sync_min_interval_hours == float(value)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "invalid", "1e12", "1e8"])
def test_invalid_intervals_rejected(monkeypatch, value):
    monkeypatch.setenv("BANK_SYNC_MIN_INTERVAL_HOURS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, _secrets_dir=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [4, 6])
async def test_automatic_sync_honors_interval_and_status(
    session, test_user, test_workspace, hours,
):
    now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    rows = []
    # At the exact boundary the existing strict-older-than behavior is retained.
    cases = [(hours - 0.01, "active", False), (hours, "active", False),
             (hours + 0.01, "active", True), (None, "active", True),
             (hours + 1, "error", True), (hours + 1, "expired", False)]
    for age, status, eligible in cases:
        conn = BankConnection(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            provider="test", external_id=str(uuid.uuid4()), institution_name="Test bank",
            credentials={"synthetic": True}, status=status, created_at=now,
            last_sync_at=now - timedelta(hours=age) if age is not None else None,
        )
        session.add(conn)
        rows.append((conn.id, eligible))
    await session.commit()
    expected = {conn_id for conn_id, eligible in rows if eligible}
    engine = AsyncMock()
    sync_one = AsyncMock()
    settings = Settings(bank_sync_min_interval_hours=hours, _env_file=None, _secrets_dir=None)
    with patch("app.tasks.sync_tasks._make_session_maker", return_value=(engine, lambda: session)), \
         patch("app.tasks.sync_tasks.get_settings", return_value=settings), \
         patch("app.tasks.sync_tasks.get_timezone", new_callable=AsyncMock,
               return_value=ZoneInfo("UTC")), \
         patch("app.tasks.sync_tasks.datetime") as clock, \
         patch("app.tasks.sync_tasks._sync_one", sync_one):
        clock.now.return_value = now
        assert (await _sync_all())["synced"] == len(expected)
    assert {call.args[1] for call in sync_one.await_args_list} == expected
    engine.dispose.assert_awaited_once()
