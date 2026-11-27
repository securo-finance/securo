from datetime import datetime, timedelta, timezone

from app.tasks.sync_tasks import _should_trigger_provider_refresh


def test_should_trigger_provider_refresh_daily():
    now = datetime(2026, 6, 29, tzinfo=timezone.utc)

    assert _should_trigger_provider_refresh(None, now)
    assert _should_trigger_provider_refresh({"last_provider_refresh_at": "invalid"}, now)
    assert _should_trigger_provider_refresh(
        {"last_provider_refresh_at": (now - timedelta(hours=20)).isoformat()}, now
    )
    assert not _should_trigger_provider_refresh(
        {"last_provider_refresh_at": (now - timedelta(hours=4)).isoformat()}, now
    )
