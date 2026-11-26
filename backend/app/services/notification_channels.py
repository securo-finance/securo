"""Where a notification goes out, and what each person wants on each (ADR 0041).

Inside the app is a channel that is always there and sends nothing: the rows
are the message. Any other is a `Channel` in `_CHANNELS`, and nothing in
`notification_service` knows which: it says a notification asks for attention,
`queue` writes one delivery per channel the person wants it on, and `deliver`
sends them. With no channel registered, as today, both do nothing.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationDelivery, NotificationPreference
from app.models.user import User

logger = logging.getLogger(__name__)

IN_APP = "in_app"
#: Waits between tries; past the last one the delivery has failed.
_RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6))
_MAX_ATTEMPTS = len(_RETRY_AFTER) + 1
#: How long a try may take before another worker may take it over.
_LEASE = timedelta(minutes=5)


@dataclass(frozen=True)
class Message:
    """What a channel gets: facts, as the client does. The channel writes the
    text, in `locale`, and how much of it: an SMS is not an e-mail."""

    user: User
    workspace_id: uuid.UUID
    kind: str
    data: dict[str, Any]
    locale: Optional[str]


class Refused(Exception):
    """Trying again won't help (an address that doesn't exist). `code` is kept, never the service's words."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code[:64]


class Channel(ABC):
    """A way out of the app. One gated by its own setting (ADR 0004, 0016)."""

    #: Stored with preferences and deliveries, so it never changes.
    name: str

    @abstractmethod
    async def reaches(self, session: AsyncSession, user: User) -> bool:
        """Whether it is set up on this instance and knows where to find them."""

    @abstractmethod
    async def send(self, session: AsyncSession, message: Message) -> None:
        """Send it, or raise: `Refused` to give up, anything else to try again."""


_CHANNELS: dict[str, Channel] = {}


def register(channel: Channel) -> None:
    if channel.name == IN_APP:
        raise ValueError("the app itself is not a channel to register")
    _CHANNELS[channel.name] = channel


def external() -> list[str]:
    return list(_CHANNELS)


def _register_configured() -> None:
    """Register each channel whose setting is on. None is shipped yet.

    Run when this module is imported, so the API, the worker, the MCP server
    and the CLI all know the same ones: a notification raised where a channel
    isn't registered would never be queued for it. A channel's own imports go
    under its `if`, so one that is off costs nothing (ADR 0004)."""


_register_configured()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- Preferences ------------------------------------------------------------


def _default(channel: str) -> bool:
    """Inside the app unless turned off; anywhere else only when asked for."""
    return channel == IN_APP


async def available_to(session: AsyncSession, user: User) -> list[str]:
    """The channels that reach this person, the app first."""
    return [IN_APP, *[name for name, channel in _CHANNELS.items() if await channel.reaches(session, user)]]


async def preferences(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> dict[tuple[str, str], bool]:
    """What someone chose, by (kind, channel)."""
    rows = await session.execute(
        select(NotificationPreference).where(
            NotificationPreference.workspace_id == workspace_id, NotificationPreference.user_id == user_id
        )
    )
    return {(row.kind, row.channel): row.enabled for row in rows.scalars()}


def wants(chosen: dict[tuple[str, str], bool], kind: str, channel: str) -> bool:
    return chosen.get((kind, channel), _default(channel))


async def set_preference(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, kind: str, channel: str, *, enabled: bool
) -> None:
    mine = (
        NotificationPreference.workspace_id == workspace_id,
        NotificationPreference.user_id == user_id,
        NotificationPreference.kind == kind,
        NotificationPreference.channel == channel,
    )
    row = await session.scalar(select(NotificationPreference).where(*mine))
    if row is None:
        try:
            async with session.begin_nested():
                session.add(
                    NotificationPreference(
                        workspace_id=workspace_id, user_id=user_id, kind=kind, channel=channel, enabled=enabled
                    )
                )
        except IntegrityError:
            # Another request chose first: the last one wins.
            row = await session.scalar(select(NotificationPreference).where(*mine))
    if row is not None:
        row.enabled = enabled
    await session.commit()


# --- Deliveries -------------------------------------------------------------


async def queue(session: AsyncSession, notifications: Iterable[Notification]) -> None:
    """Put notifications that ask for attention on their way out.

    In the caller's transaction: what is rolled back is never sent. Never
    fails the caller, as the app already has the notification."""
    if not _CHANNELS:
        return
    try:
        async with session.begin_nested():
            for notification in notifications:
                user = await session.get(User, notification.user_id)
                if user is None:
                    continue
                chosen = await preferences(session, notification.workspace_id, notification.user_id)
                for name, channel in _CHANNELS.items():
                    if wants(chosen, notification.kind, name) and await channel.reaches(session, user):
                        session.add(
                            NotificationDelivery(
                                workspace_id=notification.workspace_id,
                                notification_id=notification.id,
                                channel=name,
                                notified_at=notification.notified_at,
                                next_attempt_at=_now(),
                            )
                        )
    except Exception:
        logger.exception("Could not queue notifications for delivery")


async def deliver(session: AsyncSession, *, limit: int = 100) -> int:
    """Send what is due, one by one; one that fails waits without holding the rest."""
    sent = 0
    for _ in range(limit):
        delivery = await _claim(session)
        if delivery is None:
            break
        sent += await _send(session, delivery)
    return sent


async def _claim(session: AsyncSession) -> Optional[NotificationDelivery]:
    while True:
        now = _now()
        delivery = await session.scalar(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status == "pending",
                NotificationDelivery.next_attempt_at <= now,
                NotificationDelivery.channel.in_(external()),
            )
            .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if delivery is None:
            return None
        if delivery.attempts < _MAX_ATTEMPTS:
            # Taken before sending, so a worker that dies midway leaves it to another.
            delivery.attempts += 1
            delivery.next_attempt_at = now + _LEASE
            await session.commit()
            return delivery
        # Its last try was taken and never came back.
        delivery.status, delivery.error = "failed", "abandoned"
        await session.commit()


async def _send(session: AsyncSession, delivery: NotificationDelivery) -> bool:
    delivery_id, attempt = delivery.id, delivery.attempts
    channel = _CHANNELS[delivery.channel]
    outcome: dict[str, Any]
    try:
        message = await _message(session, delivery, channel)
        if message is None:
            outcome = {"status": "skipped"}
        else:
            await channel.send(session, message)
            outcome = {"status": "sent", "sent_at": _now(), "error": None}
    except Refused as exc:
        outcome = {"status": "failed", "error": exc.code}
    except Exception as exc:
        error = type(exc).__name__[:64]
        logger.warning("Delivery %s through %s failed: %s", delivery_id, channel.name, error)
        if attempt >= _MAX_ATTEMPTS:
            outcome = {"status": "failed", "error": error}
        else:
            outcome = {"error": error, "next_attempt_at": _now() + _RETRY_AFTER[attempt - 1]}
    # Whatever the channel left half done in the session goes; the outcome stays,
    # unless the lease ran out and the try belongs to another worker by now.
    await session.rollback()
    await session.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.status == "pending",
            NotificationDelivery.attempts == attempt,
        )
        .values(**outcome)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return outcome.get("status") == "sent"


async def _message(
    session: AsyncSession, delivery: NotificationDelivery, channel: Channel
) -> Optional[Message]:
    """The message, unless there is nothing left to say: read or over by now,
    asked again since, turned off, or no longer someone's to hear."""
    from app.services import workspace_service

    row = await session.get(Notification, delivery.notification_id, populate_existing=True)
    if row is None or row.read_at is not None or row.resolved_at is not None:
        return None
    if _aware(row.notified_at) != _aware(delivery.notified_at):
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active or not await channel.reaches(session, user):
        return None
    if not wants(await preferences(session, row.workspace_id, row.user_id), row.kind, channel.name):
        return None
    if await workspace_service.get_membership(
        session, row.workspace_id, user.id
    ) is None and not await workspace_service.is_workspace_manager(session, row.workspace_id, user.id):
        return None
    return Message(
        user=user,
        workspace_id=row.workspace_id,
        kind=row.kind,
        data=dict(row.data),
        locale=(user.preferences or {}).get("language"),
    )
