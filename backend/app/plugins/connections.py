"""Connections a workspace makes through a plugin's integrations.

A connection is what the person set up (a name, the fields of the
integration's form) plus what the plugin keeps between calls. The backend
never runs the plugin: it seals the secrets, and for every call opens them
and hands them to the plugin runner with the connection's settings, its
cursor and, for plugins that declared `api_scopes`, a token limited to those
scopes. The plugin answers with a new cursor and, after signing in or
refreshing, new secrets to seal.

Sign-in (`oauth`): the plugin builds the link to the service; the service
sends the browser back to Securo's `/plugins/oauth/callback` page, and the
plugin trades the code for a token. The state is single use and bound to the
person and workspace that started it.
"""
from __future__ import annotations

import logging
import secrets as secrets_module
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core import redis as redis_store
from app.models.access_token import AccessToken
from app.models.plugin_connection import PluginConnection
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import runner, vault
from app.plugins.manifest import Integration, Manifest, localized, parse_manifest
from app.services import access_token_service, oauth_state

logger = logging.getLogger(__name__)

#: Keys of `secrets` that belong to Securo, not to the plugin.
_API_TOKEN = "__securo_api_token"
#: Longest wait after repeated failures before the next scheduled sync.
_MAX_BACKOFF = timedelta(hours=24)
STATUSES = ("pending", "active", "error", "needs_auth")


class PluginConnectionError(Exception):
    """The request can't be carried out; the message is safe to show."""


class ConnectionNotFound(Exception):
    pass


def redirect_uri() -> str:
    """Where a service sends the browser back after signing in: register this with it."""
    return f"{get_settings().frontend_url.rstrip('/')}/plugins/oauth/callback"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


async def _installed(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> tuple[WorkspacePlugin, Manifest]:
    record = await session.scalar(
        select(WorkspacePlugin).where(WorkspacePlugin.workspace_id == workspace_id, WorkspacePlugin.plugin_id == plugin_id)
    )
    if record is None or not record.manifest:
        raise PluginConnectionError(f"{plugin_id} isn't installed in this workspace")
    return record, parse_manifest(record.manifest)


def _integration(manifest: Manifest, key: str) -> Integration:
    found = next((i for i in manifest.contributes.integrations if i.key == key), None)
    if found is None:
        raise PluginConnectionError(f"{manifest.id} has no integration {key}")
    return found


async def list_connections(session: AsyncSession, workspace_id: uuid.UUID) -> list[PluginConnection]:
    rows = await session.scalars(
        select(PluginConnection)
        .where(PluginConnection.workspace_id == workspace_id)
        .order_by(PluginConnection.plugin_id, PluginConnection.created_at)
    )
    return list(rows.all())


async def get_connection(session: AsyncSession, workspace_id: uuid.UUID, connection_id: uuid.UUID) -> PluginConnection:
    connection = await session.get(PluginConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise ConnectionNotFound
    return connection


async def create(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    can_write: bool,
    plugin_id: str,
    integration_key: str,
    name: str,
    values: dict[str, Any],
    lang: str,
) -> tuple[PluginConnection, str | None]:
    """A new connection, and the sign-in link when the integration uses OAuth."""
    record, manifest = await _installed(session, workspace_id, plugin_id)
    integration = _integration(manifest, integration_key)
    config, secrets = _split(integration, values)
    name = name.strip() or localized(integration.name, lang)

    if manifest.api_scopes:
        # The token shows in the workspace's access tokens, named after the
        # plugin, and is revoked with the connection.
        token, raw = await access_token_service.create_token(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            can_write=can_write,
            name=f"{localized(manifest.name, lang)}: {name}"[:100],
            scopes=[s.scope for s in manifest.api_scopes],
            expires_in_days=None,
        )
        secrets[_API_TOKEN] = raw
        token_id: uuid.UUID | None = token.id
    else:
        token_id = None

    connection = PluginConnection(
        workspace_id=workspace_id,
        plugin_id=plugin_id,
        integration=integration.key,
        name=name[:100],
        status="needs_auth" if integration.auth == "oauth" else "pending",
        config=config,
        secrets=vault.seal(secrets),
        state={},
        sync_minutes=integration.sync_minutes,
        access_token_id=token_id,
        failures=0,
        created_by=user_id,
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    link = None
    if integration.auth == "oauth":
        try:
            link = await authorize_url(session, connection, user_id=user_id, record=record)
        except Exception:
            # Without a sign-in link the connection can't be finished: don't leave it (or its token) behind.
            await delete(session, connection)
            raise
    return connection, link


def _split(integration: Integration, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The form's values as settings and secrets; refuses unknown or missing fields."""
    fields = {f.key: f for f in integration.fields}
    unknown = set(values) - set(fields)
    if unknown:
        raise PluginConnectionError(f"unknown fields: {', '.join(sorted(unknown))}")
    config: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for key, field in fields.items():
        value = values.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            if field.required:
                raise PluginConnectionError(f"{key} is required")
            continue
        if not isinstance(value, str) or len(value) > 20000:
            raise PluginConnectionError(f"{key} must be text")
        (secrets if field.secret else config)[key] = value
    return config, secrets


def _securo(connection: PluginConnection, secrets: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "api_url": get_settings().plugins_api_url,
        "workspace_id": str(connection.workspace_id),
        "connection_id": str(connection.id),
    }
    if secrets.get(_API_TOKEN):
        context["token"] = secrets[_API_TOKEN]
    return context


async def _call(
    session: AsyncSession, connection: PluginConnection, method: str, params: dict[str, Any], record: WorkspacePlugin | None = None
) -> tuple[Any, dict[str, Any]]:
    """Call the connection's plugin; returns the result and the opened secrets.

    A plugin's error message never carries one of its secrets back out: any
    secret value in it is masked before it is stored or shown.
    """
    if record is None:
        record, _ = await _installed(session, connection.workspace_id, connection.plugin_id)
    try:
        secrets = vault.unseal(connection.secrets)
    except vault.SealBroken as exc:
        raise PluginConnectionError("its secrets can't be read anymore; set it up again") from exc
    plugin_secrets = {k: v for k, v in secrets.items() if not k.startswith("__securo_")}

    async def run() -> Any:
        async with runner.RunnerClient() as client:
            return await client.call(
                connection.plugin_id,
                method,
                params,
                secrets=plugin_secrets,
                config=dict(connection.config or {}),
                securo=_securo(connection, secrets),
                commit=record.commit,
            )

    try:
        try:
            result = await run()
        except runner.RunnerRefused as exc:
            # The runner lost this commit (a new volume, a race with a removal):
            # put it back from the catalog when the catalog still pins it, once.
            if exc.status != 404 or not await _reinstall(record):
                raise
            result = await run()
    except runner.PluginCallFailed as exc:
        raise runner.PluginCallFailed(exc.kind, mask(exc.message, secrets)) from None
    return result, secrets


def mask(message: str, secrets: dict[str, Any]) -> str:
    """`message` without any of the secret values (or NUL bytes, which Postgres refuses)."""
    for value in sorted((str(v) for v in secrets.values() if v), key=len, reverse=True):
        if len(value) >= 4:
            message = message.replace(value, "***")
    return message.replace("\x00", "")


async def _reinstall(record: WorkspacePlugin) -> bool:
    from app.plugins import index, service

    if not record.manifest:
        return False
    try:
        async with index.http_client() as http:
            entry = next((e for e in await index.fetch_index(http) if e.id == record.plugin_id), None)
        if entry is None or entry.commit != record.commit:
            return False  # the catalog moved on: the owner has to update
        await service.install_on_runner(entry, parse_manifest(record.manifest))
        logger.info("Reinstalled %s at %s on the plugin runner", record.plugin_id, record.commit)
        return True
    except (index.PluginIndexError, runner.RunnerUnavailable, runner.RunnerRefused) as exc:
        logger.warning("Couldn't reinstall %s on the plugin runner: %s", record.plugin_id, exc)
        return False


async def authorize_url(
    session: AsyncSession, connection: PluginConnection, *, user_id: uuid.UUID, record: WorkspacePlugin | None = None
) -> str:
    """The service's sign-in link for this connection, built by the plugin."""
    state = await oauth_state.store_state(
        {
            "kind": "plugin",
            "connection_id": str(connection.id),
            "user_id": str(user_id),
            "workspace_id": str(connection.workspace_id),
        }
    )
    result, _ = await _call(session, connection, "oauth_url", {"redirect_uri": redirect_uri(), "state": state}, record)
    url = result.get("url") if isinstance(result, dict) else result
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise PluginConnectionError("the plugin didn't return an https sign-in link")
    return url


async def finish_oauth(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, code: str, state: str
) -> PluginConnection:
    payload = await oauth_state.consume_state(state)
    if (
        not payload
        or payload.get("kind") != "plugin"
        or payload.get("user_id") != str(user_id)
        or payload.get("workspace_id") != str(workspace_id)
    ):
        raise PluginConnectionError("this sign-in link expired or wasn't started here; try again")
    connection = await get_connection(session, workspace_id, uuid.UUID(payload["connection_id"]))
    result, secrets = await _call(session, connection, "oauth_exchange", {"code": code, "redirect_uri": redirect_uri()})
    _keep(connection, result, secrets)
    connection.status = "pending"
    connection.last_error = None
    await session.commit()
    return connection


def _keep(connection: PluginConnection, result: Any, secrets: dict[str, Any]) -> None:
    """Store what a call handed back: new secrets (merged) and a new cursor."""
    if not isinstance(result, dict):
        return
    new_secrets = result.get("secrets")
    if isinstance(new_secrets, dict) and new_secrets:
        merged = {**secrets, **{k: v for k, v in new_secrets.items() if not str(k).startswith("__securo_")}}
        connection.secrets = vault.seal(merged)
    new_state = result.get("state")
    if isinstance(new_state, dict):
        connection.state = new_state


async def sync(session: AsyncSession, connection: PluginConnection) -> PluginConnection:
    """Run the plugin's `sync` once and record how it went.

    One sync per connection at a time (the schedule, "sync now" and the first
    sync can meet): a second one returns the connection as it is.
    """
    lock = f"plugin-sync:{connection.id}"
    if not await _acquire(lock):
        return connection
    try:
        params = {
            "state": dict(connection.state or {}),
            "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        }
        try:
            result, secrets = await _call(session, connection, "sync", params)
        except runner.PluginCallFailed as exc:
            _failed(connection, f"{exc.kind}: {exc.message}", needs_auth=exc.kind in ("NeedsAuth", "AuthError"))
        except (runner.RunnerUnavailable, runner.RunnerRefused, PluginConnectionError) as exc:
            _failed(connection, str(exc), needs_auth=False)
        else:
            _keep(connection, result, secrets)
            connection.status = "active"
            connection.last_error = None
            connection.failures = 0
            connection.last_sync_at = _now()
        await session.commit()
        return connection
    finally:
        await _release(lock)


#: Longest a sync can hold its lock, should the process die mid-sync.
_LOCK_SECONDS = 15 * 60


async def _acquire(key: str) -> bool:
    redis = await redis_store.get_redis()
    return bool(await redis.set(key, "1", nx=True, ex=_LOCK_SECONDS))


async def _release(key: str) -> None:
    redis = await redis_store.get_redis()
    await redis.delete(key)


def _failed(connection: PluginConnection, message: str, *, needs_auth: bool) -> None:
    connection.status = "needs_auth" if needs_auth else "error"
    connection.last_error = message.replace("\x00", "")[:500]
    connection.failures = (connection.failures or 0) + 1
    # Counts as an attempt, so the schedule backs off instead of retrying at once.
    connection.last_sync_at = _now()


def due(connection: PluginConnection, now: datetime | None = None) -> bool:
    """Whether the schedule should sync it now (backing off after failures)."""
    if connection.sync_minutes is None or connection.status == "needs_auth":
        return False
    last = _aware(connection.last_sync_at)
    if last is None:
        return True
    wait = timedelta(minutes=connection.sync_minutes)
    if connection.failures:
        wait = min(wait * (2 ** min(connection.failures, 10)), max(_MAX_BACKOFF, wait))
    return (now or _now()) - last >= wait


async def delete(session: AsyncSession, connection: PluginConnection) -> None:
    await _revoke_token(session, connection)
    await session.delete(connection)
    await session.commit()


async def delete_all(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> int:
    """Every connection of a plugin being removed from a workspace (not committed)."""
    rows = await session.scalars(
        select(PluginConnection).where(
            PluginConnection.workspace_id == workspace_id, PluginConnection.plugin_id == plugin_id
        )
    )
    count = 0
    for connection in rows.all():
        await _revoke_token(session, connection)
        await session.delete(connection)
        count += 1
    return count


async def follow_manifest(session: AsyncSession, workspace_id: uuid.UUID, manifest: Manifest, *, lang: str) -> None:
    """After an update the owner agreed to, connections act with the new scopes (not committed).

    Each connection's token gets exactly the manifest's scopes. A plugin that
    starts declaring scopes gets a token per connection; one that stops has
    its tokens revoked.
    """
    scopes = [s.scope for s in manifest.api_scopes]
    rows = await session.scalars(
        select(PluginConnection).where(
            PluginConnection.workspace_id == workspace_id, PluginConnection.plugin_id == manifest.id
        )
    )
    for connection in rows.all():
        token = await session.get(AccessToken, connection.access_token_id) if connection.access_token_id else None
        if token is not None and token.revoked_at is None and scopes:
            token.scopes = list(scopes)
            continue
        try:
            secrets = vault.unseal(connection.secrets)
        except vault.SealBroken:
            continue  # set up again anyway; nothing to hand a token to
        await _revoke_token(session, connection)
        secrets.pop(_API_TOKEN, None)
        connection.access_token_id = None
        if scopes and connection.created_by is not None:
            raw = access_token_service.TOKEN_PREFIX + secrets_module.token_urlsafe(32)
            fresh = AccessToken(
                workspace_id=workspace_id,
                user_id=connection.created_by,
                name=f"{localized(manifest.name, lang)}: {connection.name}"[:100],
                token_prefix=raw[: len(access_token_service.TOKEN_PREFIX) + 6],
                token_hash=access_token_service.hash_token(raw),
                scopes=list(scopes),
                created_at=_now(),
            )
            session.add(fresh)
            await session.flush()
            connection.access_token_id = fresh.id
            secrets[_API_TOKEN] = raw
        connection.secrets = vault.seal(secrets)


async def _revoke_token(session: AsyncSession, connection: PluginConnection) -> None:
    if connection.access_token_id is None:
        return
    token = await session.get(AccessToken, connection.access_token_id)
    if token is not None and token.revoked_at is None:
        token.revoked_at = _now()
