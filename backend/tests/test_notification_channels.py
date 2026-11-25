"""Channels outside the app (ADR 0041), with a made-up one: none is shipped yet."""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.notification import Notification, NotificationDelivery
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import notification_channels as channels
from app.services import notification_service

pytestmark = pytest.mark.asyncio


class Pigeon(channels.Channel):
    name = "pigeon"

    def __init__(self) -> None:
        self.sent: list[channels.Message] = []
        self.fails: Exception | None = None
        self.finds = True

    async def reaches(self, session: AsyncSession, user: User) -> bool:
        return self.finds

    async def send(self, session: AsyncSession, message: channels.Message) -> None:
        if self.fails is not None:
            raise self.fails
        self.sent.append(message)


@pytest.fixture
def pigeon(monkeypatch) -> Pigeon:
    channel = Pigeon()
    monkeypatch.setattr(channels, "_CHANNELS", {})
    channels.register(channel)
    return channel


async def _want(session: AsyncSession, workspace: Workspace, user: User, kind: str = "connection_error") -> None:
    await channels.set_preference(session, workspace.id, user.id, kind, "pigeon", enabled=True)


async def _break(session: AsyncSession, workspace: Workspace, connection: BankConnection) -> Notification:
    connection.settings = {
        "last_sync_failure": {
            "code": "consent_expired", "at": datetime.now(timezone.utc).isoformat(), "detail": None,
        }
    }
    await session.commit()
    await notification_service.evaluate_workspace(session, workspace.id)
    row = await session.scalar(select(Notification).where(Notification.kind == "connection_error"))
    assert row is not None
    return row


async def _deliveries(session: AsyncSession) -> list[NotificationDelivery]:
    query = select(NotificationDelivery).order_by(NotificationDelivery.notified_at)
    return list((await session.execute(query.execution_options(populate_existing=True))).scalars())


async def _due(session: AsyncSession) -> None:
    for row in await _deliveries(session):
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()


async def test_nothing_is_queued_without_a_channel(
    session: AsyncSession, test_workspace: Workspace, test_connection: BankConnection
):
    assert channels.external() == []
    await _break(session, test_workspace, test_connection)
    assert await _deliveries(session) == [] and await channels.deliver(session) == 0


async def test_outside_the_app_is_only_what_was_asked_for(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    await _break(session, test_workspace, test_connection)
    assert await _deliveries(session) == []


async def test_a_notification_is_sent_once(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    test_user.preferences = {**(test_user.preferences or {}), "language": "pt-BR"}
    await _want(session, test_workspace, test_user)
    row = await _break(session, test_workspace, test_connection)
    await notification_service.evaluate_workspace(session, test_workspace.id)
    expected = ("connection_error", dict(row.data), "pt-BR", test_workspace.id)
    assert await channels.deliver(session) == 1
    assert await channels.deliver(session) == 0
    [message] = pigeon.sent
    assert (message.kind, message.data, message.locale, message.workspace_id) == expected
    [delivery] = await _deliveries(session)
    assert delivery.status == "sent" and delivery.sent_at is not None


async def test_a_reminder_is_sent_again(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    await _want(session, test_workspace, test_user)
    workspace_id = test_workspace.id
    row = await _break(session, test_workspace, test_connection)
    await channels.deliver(session)
    await session.refresh(row)
    row.notified_at = datetime.now(timezone.utc) - timedelta(days=8)
    await session.commit()
    await notification_service.evaluate_workspace(session, workspace_id)
    assert await channels.deliver(session) == 1
    assert len(pigeon.sent) == 2


async def test_a_failure_is_tried_again_and_then_given_up(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    await _want(session, test_workspace, test_user)
    await _break(session, test_workspace, test_connection)
    pigeon.fails = TimeoutError("to someone@example.com")
    assert await channels.deliver(session) == 0
    # It waits its turn instead of being tried in a loop.
    assert await channels.deliver(session) == 0
    [delivery] = await _deliveries(session)
    assert (delivery.status, delivery.attempts, delivery.error) == ("pending", 1, "TimeoutError")
    for _ in range(4):
        await _due(session)
        await channels.deliver(session)
    [delivery] = await _deliveries(session)
    assert (delivery.status, delivery.attempts) == ("failed", 5)
    await _due(session)
    pigeon.fails = None
    assert await channels.deliver(session) == 0


async def test_a_try_that_never_came_back_still_counts(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    """A worker that dies after taking it leaves it pending until the lease runs out."""
    await _want(session, test_workspace, test_user)
    await _break(session, test_workspace, test_connection)
    for _ in range(5):
        assert await channels._claim(session) is not None
        await _due(session)
    assert await channels.deliver(session) == 0 and pigeon.sent == []
    [delivery] = await _deliveries(session)
    assert (delivery.status, delivery.attempts, delivery.error) == ("failed", 5, "abandoned")


@pytest.mark.parametrize("late", ["sent", "failed", "skipped"])
async def test_a_late_outcome_does_not_overwrite_a_newer_try(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection,
    pigeon: Pigeon, late: str,
):
    await _want(session, test_workspace, test_user)
    row = await _break(session, test_workspace, test_connection)
    first = await channels._claim(session)
    assert first is not None
    session.expunge(first)
    # Its lease ran out and another worker took the next try.
    await _due(session)
    assert await channels._claim(session) is not None
    if late == "failed":
        pigeon.fails = channels.Refused("gone")
    elif late == "skipped":
        row.read_at = datetime.now(timezone.utc)
        await session.commit()
    await channels._send(session, first)
    [delivery] = await _deliveries(session)
    assert (delivery.status, delivery.attempts, delivery.error) == ("pending", 2, None)


async def test_one_that_recovers_is_sent(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    await _want(session, test_workspace, test_user)
    await _break(session, test_workspace, test_connection)
    pigeon.fails = TimeoutError()
    await channels.deliver(session)
    pigeon.fails = None
    await _due(session)
    assert await channels.deliver(session) == 1


async def test_a_refusal_is_final(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection, pigeon: Pigeon
):
    await _want(session, test_workspace, test_user)
    await _break(session, test_workspace, test_connection)
    pigeon.fails = channels.Refused("no_such_address")
    await channels.deliver(session)
    [delivery] = await _deliveries(session)
    assert (delivery.status, delivery.error) == ("failed", "no_such_address")


@pytest.mark.parametrize("why", ["read", "resolved", "raised_again", "turned_off", "inactive", "left", "unreachable"])
async def test_what_has_nothing_left_to_say_is_not_sent(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection,
    pigeon: Pigeon, why: str,
):
    await _want(session, test_workspace, test_user)
    row = await _break(session, test_workspace, test_connection)
    now = datetime.now(timezone.utc)
    if why == "read":
        row.read_at = now
    elif why == "resolved":
        row.resolved_at = now
    elif why == "raised_again":
        row.notified_at = now + timedelta(minutes=1)
    elif why == "turned_off":
        await channels.set_preference(
            session, test_workspace.id, test_user.id, "connection_error", "pigeon", enabled=False
        )
    elif why == "inactive":
        test_user.is_active = False
    elif why == "left":
        await session.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == test_workspace.id, WorkspaceMember.user_id == test_user.id
            )
        )
    elif why == "unreachable":
        pigeon.finds = False
    await session.commit()
    assert await channels.deliver(session) == 0 and pigeon.sent == []
    [delivery] = await _deliveries(session)
    assert delivery.status == "skipped"


async def test_a_channel_that_breaks_never_costs_the_notification(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection,
    pigeon: Pigeon, monkeypatch,
):
    async def boom(*_args) -> bool:
        raise RuntimeError("boom")

    await _want(session, test_workspace, test_user)
    monkeypatch.setattr(pigeon, "reaches", boom)
    await _break(session, test_workspace, test_connection)
    assert await _deliveries(session) == []


async def test_the_app_and_a_channel_are_chosen_apart(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace,
    test_connection: BankConnection, pigeon: Pigeon,
):
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    async def choose(channel: str, enabled: bool) -> None:
        response = await client.put(
            f"/api/notifications/preferences/connection_error/{channel}", json={"enabled": enabled}, headers=headers
        )
        assert response.status_code == 204

    async def chosen() -> dict[str, bool]:
        rows = (await client.get("/api/notifications/preferences", headers=headers)).json()
        return next(row["channels"] for row in rows if row["kind"] == "connection_error")

    assert await chosen() == {"in_app": True, "pigeon": False}
    await choose("pigeon", True)
    await choose("in_app", False)
    assert await chosen() == {"in_app": False, "pigeon": True}
    await _break(session, test_workspace, test_connection)
    assert (await client.get("/api/notifications", headers=headers)).json()["total"] == 0
    assert await channels.deliver(session) == 1

    # A channel that doesn't reach them is neither offered nor taken.
    pigeon.finds = False
    assert await chosen() == {"in_app": False}
    refused = await client.put(
        "/api/notifications/preferences/connection_error/pigeon", json={"enabled": True}, headers=headers
    )
    assert refused.status_code == 404
