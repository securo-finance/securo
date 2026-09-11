import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.bank_connection import BankConnection
from app.tasks.sync_tasks import _should_trigger_provider_refresh, _sync_one


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


@pytest.mark.asyncio
async def test_sync_one_forwards_provider_refresh(session, test_user, test_workspace):
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="test",
        external_id="provider-conn",
        institution_name="Test Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    def session_maker():
        return session

    with patch(
        "app.tasks.sync_tasks.connection_service.sync_connection",
        new_callable=AsyncMock,
    ) as sync_connection:
        await _sync_one(
            session_maker,
            conn.id,
            test_user.id,
            trigger_provider_refresh=True,
        )

    sync_connection.assert_awaited_once_with(
        session,
        conn.id,
        test_workspace.id,
        test_user.id,
        trigger_provider_refresh=True,
    )
