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

Provider integrations (`provides`): the connection comes with a bank
connection of its own, and `sync` returns records that Securo writes through
the bank sync pipeline (`app.plugins.contract`, `app.plugins.provider`). The
plugin's cursor is kept only once its records are written, so a sync that
fails is fetched again.
"""
from __future__ import annotations

import base64
import binascii
import logging
import secrets as secrets_module
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core import redis as redis_store
from app.models.access_token import AccessToken
from app.models.bank_connection import LAST_SYNC_FAILURE_KEY, BankConnection
from app.models.plugin_connection import PluginConnection, PluginConnectionRoutine
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import logos, runner, vault
from app.plugins.contract import PayloadError, parse_payload
from app.plugins.manifest import FormField, Integration, Manifest, localized, parse_manifest
from app.plugins.provider import PluginPayloadProvider, provider_name
from app.services import access_token_service, notification_service, oauth_state

logger = logging.getLogger(__name__)

#: Keys of `secrets` that belong to Securo, not to the plugin.
_API_TOKEN = "__securo_api_token"
#: Longest wait after repeated failures before the next scheduled sync.
_MAX_BACKOFF = timedelta(hours=24)
STATUSES = ("pending", "active", "error", "needs_auth")
#: A plugin connection's status, as its bank connection shows it.
BANK_STATUS = {"pending": "active", "active": "active", "error": "error", "needs_auth": "expired"}
#: How far back the plugin is asked to re-read, as the bank sync does.
_SINCE_OVERLAP = timedelta(days=14)
_BANK_GONE = "its bank connection is gone; remove it and connect again"


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
        id=uuid.uuid4(),
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
    if integration.is_provider:
        await _add_bank(session, connection, localized(manifest.name, lang), user_id)
    session.add(connection)
    await session.flush()
    await _follow_routines(session, connection, integration)
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


async def _add_bank(session: AsyncSession, connection: PluginConnection, institution: str, user_id: uuid.UUID) -> None:
    """The bank connection a provider integration syncs into, listed under Bank Connections from the start.

    The institution is named after the plugin (an "Interactive Brokers"
    plugin), as a native connection is named after its bank.
    """
    bank = BankConnection(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=connection.workspace_id,
        provider=provider_name(connection.plugin_id),
        external_id=str(connection.id),
        institution_name=institution[:255],
        display_name=connection.name,
        credentials={},
        settings={},
        status=BANK_STATUS.get(connection.status, "error"),
    )
    session.add(bank)
    await session.flush()
    connection.bank_connection_id = bank.id


async def _drop_bank(session: AsyncSession, connection: PluginConnection, *, keep_records: bool) -> None:
    """Take the connection's bank connection away (not committed).

    Someone removing the connection disconnects it exactly as a native one is,
    accounts and their transactions included. When the plugin goes instead
    (removed, or an update that stops providing), the records stay: the
    accounts carry on as manual ones, so no release of a plugin can take
    anyone's history with it.
    """
    if connection.bank_connection_id is None:
        return
    from app.services import connection_service

    bank_id, connection.bank_connection_id = connection.bank_connection_id, None
    if keep_records:
        await connection_service.unlink_connection(session, bank_id, connection.workspace_id)
    else:
        await connection_service.delete_connection(session, bank_id, connection.workspace_id, commit=False)


async def for_bank_connection(
    session: AsyncSession, workspace_id: uuid.UUID, bank_connection_id: uuid.UUID
) -> PluginConnection | None:
    """The plugin connection that syncs a bank connection, if a plugin does."""
    return await session.scalar(
        select(PluginConnection).where(
            PluginConnection.workspace_id == workspace_id,
            PluginConnection.bank_connection_id == bank_connection_id,
        )
    )


async def update_values(
    session: AsyncSession, connection: PluginConnection, *, name: str | None, values: dict[str, Any]
) -> tuple[PluginConnection, bool]:
    """New form values for a connection (reconnecting after a password change, say).

    A field left blank keeps what it had, so a secret never has to be typed
    again to change something else. What the plugin stored itself (a refresh
    token, say) is kept too. Returns the connection and whether any value
    changed: only then does it have to prove itself again; a rename alone
    leaves it as it was.

    Not while it syncs: the sync would save back the secrets it started with.
    """
    lock = f"plugin-sync:{connection.id}"
    if not await _acquire(lock):
        raise PluginConnectionError("this connection is syncing right now; try again in a moment")
    try:
        await session.refresh(connection)
        return await _update_values(session, connection, name=name, values=values)
    finally:
        await _release(lock)


async def _update_values(
    session: AsyncSession, connection: PluginConnection, *, name: str | None, values: dict[str, Any]
) -> tuple[PluginConnection, bool]:
    _, manifest = await _installed(session, connection.workspace_id, connection.plugin_id)
    integration = _integration(manifest, connection.integration)
    try:
        secrets = vault.unseal(connection.secrets)
    except vault.SealBroken:
        secrets = {}
    config = dict(connection.config or {})
    given = {k: v for k, v in values.items() if not (v is None or (isinstance(v, str) and not v.strip()))}
    fields = {f.key: f for f in integration.fields}
    unknown = set(values) - set(fields)
    if unknown:
        raise PluginConnectionError(f"unknown fields: {', '.join(sorted(unknown))}")
    kept = {**{k: v for k, v in config.items() if k in fields}, **{k: v for k, v in secrets.items() if k in fields}}
    changed = any(kept.get(k) != v for k, v in given.items())
    new_config, new_secrets = _split(integration, {**kept, **given})
    connection.config = new_config
    # The form's secrets replace theirs; everything else the plugin or Securo stored stays.
    connection.secrets = vault.seal({**{k: v for k, v in secrets.items() if k not in fields}, **new_secrets})
    if name is not None and name.strip():
        connection.name = name.strip()[:100]
    if changed:
        # New values have to prove themselves: it waits for the run that
        # checks them. A rename alone changes nothing about its state.
        if integration.auth != "oauth":
            connection.status = "pending"
        connection.failures = 0
        connection.last_error = None
        # Only new values end the reason the old ones left; a rename does not.
        await _forget_bank_failure(session, connection)
    if connection.bank_connection_id is not None:
        bank = await session.get(BankConnection, connection.bank_connection_id)
        if bank is not None:
            bank.display_name = connection.name
    await _mirror_status(session, connection)
    await session.commit()
    return connection, changed


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
        if field.type == "file":
            value = _file(field, value)
        elif not isinstance(value, str) or len(value) > 20000:
            raise PluginConnectionError(f"{key} must be text")
        (secrets if field.secret else config)[key] = value
    return config, secrets


def _file(field: FormField, value: Any) -> dict[str, str]:
    """A file field's value, checked: `{"name", "content"}` with the content in base64."""
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not isinstance(value.get("content"), str):
        raise PluginConnectionError(f"{field.key} must be a file")
    # Only the file's own name: a browser may send a path.
    name = value["name"].replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
    if not name:
        raise PluginConnectionError(f"{field.key} must be a file")
    if field.accept and not any(name.lower().endswith(ext) for ext in field.accept):
        raise PluginConnectionError(f"{field.key} must be a {', '.join(field.accept)} file")
    # A size check before decoding, so a huge upload is refused without holding it twice.
    if len(value["content"]) > (field.max_kb * 1024 * 4) // 3 + 4:
        raise PluginConnectionError(f"{field.key} must be at most {field.max_kb} KB")
    try:
        data = base64.b64decode(value["content"], validate=True)
    except (binascii.Error, ValueError):
        raise PluginConnectionError(f"{field.key} can't be read as a file") from None
    if not data:
        raise PluginConnectionError(f"{field.key} is empty")
    if len(data) > field.max_kb * 1024:
        raise PluginConnectionError(f"{field.key} must be at most {field.max_kb} KB")
    return {"name": name, "content": base64.b64encode(data).decode()}


def file_summary(value: Any) -> Any:
    """A stored value as the API shows it: a file's name and size, never its content."""
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        try:
            size = len(base64.b64decode(value["content"]))
        except (binascii.Error, ValueError):
            size = 0
        return {"name": value.get("name"), "size": size}
    return value


def _securo(connection: PluginConnection, secrets: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "api_url": get_settings().plugins_api_url,
        "workspace_id": str(connection.workspace_id),
        "connection_id": str(connection.id),
        "integration": connection.integration,
    }
    if secrets.get(_API_TOKEN):
        context["token"] = secrets[_API_TOKEN]
    return context


async def _call(
    session: AsyncSession,
    connection: PluginConnection,
    method: str,
    params: dict[str, Any],
    record: WorkspacePlugin | None = None,
    *,
    timeout: int | None = None,
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
                timeout=timeout,
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
    """`message` without any of the secret values, however nested (or NUL bytes, which Postgres refuses)."""
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
        elif value is not None and not isinstance(value, bool):
            values.append(str(value))

    collect(secrets)
    for value in sorted(values, key=len, reverse=True):
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
    signed_in = _keep(connection, result, secrets)
    connection.status = "pending"
    connection.last_error = None
    if signed_in:
        await _forget_bank_failure(session, connection)
    await _mirror_status(session, connection)
    await session.commit()
    return connection


async def _forget_bank_failure(session: AsyncSession, connection: PluginConnection) -> None:
    """New credentials end the reason the old ones left on the bank card (issue #1405)."""
    if connection.bank_connection_id is None:
        return
    bank = await session.get(BankConnection, connection.bank_connection_id)
    settings = (bank.settings if bank is not None else None) or {}
    if bank is not None and LAST_SYNC_FAILURE_KEY in settings:
        bank.settings = {k: v for k, v in settings.items() if k != LAST_SYNC_FAILURE_KEY}


def _keep(connection: PluginConnection, result: Any, secrets: dict[str, Any]) -> bool:
    """Store what a call handed back: new secrets (merged) and a new cursor.

    True when the call handed back secrets of its own to store."""
    if not isinstance(result, dict):
        return False
    stored = False
    new_secrets = result.get("secrets")
    if isinstance(new_secrets, dict):
        own = {k: v for k, v in new_secrets.items() if not str(k).startswith("__securo_")}
        if own:
            connection.secrets = vault.seal({**secrets, **own})
            stored = True
    new_state = result.get("state")
    if isinstance(new_state, dict):
        connection.state = new_state
    return stored


async def sync(session: AsyncSession, connection: PluginConnection) -> PluginConnection:
    """Run the plugin's `sync` once and record how it went.

    One sync per connection at a time (the schedule, "sync now" and the first
    sync can meet): a second one returns the connection as it is.
    """
    lock = f"plugin-sync:{connection.id}"
    if not await _acquire(lock):
        return connection
    try:
        # Read again under the lock: another sync may have moved the cursor or the secrets.
        await session.refresh(connection)
        params: dict[str, Any] = {
            "state": dict(connection.state or {}),
            "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        }
        provides: list[str] | None = None
        try:
            # One read of the installed version for the whole sync: an update
            # committed while the plugin runs must not change how its reply is read.
            record, manifest = await _installed(session, connection.workspace_id, connection.plugin_id)
            commit = record.commit
            provides = _provides(connection, manifest)
            if provides is not None:
                params["since"] = await _since(session, connection)
            result, secrets = await _call(session, connection, "sync", params, record)
        except runner.PluginCallFailed as exc:
            if provides is not None and exc.kind == "RateLimited":
                _rate_limited(connection, f"{exc.kind}: {exc.message}")
            else:
                _failed(connection, f"{exc.kind}: {exc.message}", needs_auth=exc.kind in ("NeedsAuth", "AuthError"))
        except (runner.RunnerUnavailable, runner.RunnerRefused, PluginConnectionError) as exc:
            _failed(connection, str(exc), needs_auth=False)
        else:
            if provides is None:
                _keep(connection, result, secrets)
                _succeeded(connection)
            else:
                await _write_payload(session, connection, provides, result, secrets, commit, manifest)
        await _mirror_status(session, connection)
        await session.commit()
        return connection
    finally:
        await _release(lock)


def _provides(connection: PluginConnection, manifest: Manifest) -> list[str] | None:
    """What the connection's integration provides, or None when it writes through the API."""
    integration = _integration(manifest, connection.integration)
    if not integration.provides:
        return None
    if connection.bank_connection_id is None:
        # Nowhere to write its records: failing says so, where syncing on
        # would drop them and move the cursor past them.
        raise PluginConnectionError(_BANK_GONE)
    return list(integration.provides)


async def _since(session: AsyncSession, connection: PluginConnection) -> str | None:
    """The first day to re-read: the bank connection's last full sync, minus the usual overlap."""
    bank = await session.get(BankConnection, connection.bank_connection_id)
    last = _aware(bank.last_sync_at) if bank is not None else None
    return (last - _SINCE_OVERLAP).date().isoformat() if last is not None else None


async def _write_payload(
    session: AsyncSession,
    connection: PluginConnection,
    provides: list[str],
    result: Any,
    secrets: dict[str, Any],
    commit: str,
    manifest: Manifest,
) -> None:
    """Write what `sync` returned through the bank sync pipeline, then keep the plugin's cursor.

    New secrets are kept whatever happens next, so a rotated token is never
    lost. The cursor is kept only once the records are written: a payload that
    is invalid, or fails to write, is fetched again on the next sync.
    """
    from app.services import connection_service

    reply = result if isinstance(result, dict) else {}
    _keep(connection, {"secrets": reply.get("secrets")}, secrets)
    await session.commit()
    logo_urls = await logos.urls(session, connection.plugin_id, commit)
    try:
        payload = parse_payload(reply.get("data"), provides, logo_urls, {h.host for h in manifest.network})
    except PayloadError as exc:
        _failed(connection, str(exc), needs_auth=False)
        return
    if payload is not None:
        bank = await session.get(BankConnection, connection.bank_connection_id)
        if bank is None:
            _failed(connection, _BANK_GONE, needs_auth=False)
            return
        connection_id, workspace_id, bank_id, user_id = connection.id, connection.workspace_id, bank.id, bank.user_id
        try:
            await connection_service.sync_connection(
                session, bank_id, workspace_id, user_id,
                provider=PluginPayloadProvider(
                    bank.provider, payload, logo_urls, scope=bank.external_id,
                    complete_feed=_integration(manifest, connection.integration).complete_feed,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the connection, retried on the next sync
            logger.exception("Writing the records of plugin connection %s failed", connection_id)
            refreshed = await session.get(PluginConnection, connection_id)
            if refreshed is None:
                return
            await session.refresh(refreshed)
            _failed(refreshed, f"Securo couldn't save what the plugin returned ({type(exc).__name__})", needs_auth=False)
            return
        await session.refresh(connection)
    _keep(connection, {"state": reply.get("state")}, secrets)
    _succeeded(connection)


def _succeeded(connection: PluginConnection) -> None:
    connection.status = "active"
    connection.last_error = None
    connection.failures = 0
    connection.last_sync_at = _now()


def _rate_limited(connection: PluginConnection, message: str) -> None:
    """The service is throttling: healthy, so no failure, and the next try waits the usual interval."""
    connection.status = "active"
    connection.last_error = message.replace("\x00", "")[:500]
    connection.last_sync_at = _now()


def rate_limited(connection: PluginConnection) -> bool:
    """Whether its last sync was throttled by the service: healthy, but nothing was pulled."""
    # A sync that succeeds clears the error, so only a throttled one is active with one.
    return connection.status == "active" and bool(connection.last_error)


async def _mirror_status(session: AsyncSession, connection: PluginConnection) -> None:
    """The bank connection shows the plugin connection's health (its last sync is the pipeline's)."""
    await notification_service.connection_changed(session, connection)
    if connection.bank_connection_id is None:
        return
    bank = await session.get(BankConnection, connection.bank_connection_id)
    if bank is not None:
        bank.status = BANK_STATUS.get(connection.status, "error")


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
    return _due(connection.last_sync_at, connection.sync_minutes, connection.failures, now)


def _due(last_run: datetime | None, minutes: int, failures: int | None, now: datetime | None) -> bool:
    last = _aware(last_run)
    if last is None:
        return True
    wait = timedelta(minutes=minutes)
    if failures:
        wait = min(wait * (2 ** min(failures, 10)), max(_MAX_BACKOFF, wait))
    return (now or _now()) - last >= wait


# Routines --------------------------------------------------------------------


def routine_due(routine: PluginConnectionRoutine, connection: PluginConnection, now: datetime | None = None) -> bool:
    """Whether the schedule should run this routine now (backing off after its own failures)."""
    if connection.status == "needs_auth":
        return False
    return _due(routine.last_run_at, routine.every_minutes, routine.failures, now)


async def integration_of(session: AsyncSession, connection: PluginConnection) -> Integration | None:
    """The connection's integration as installed, or None when it can't be read."""
    try:
        _, manifest = await _installed(session, connection.workspace_id, connection.plugin_id)
        return _integration(manifest, connection.integration)
    except (PluginConnectionError, ConnectionNotFound):
        return None


async def list_routines(session: AsyncSession, connection_ids: list[uuid.UUID]) -> list[PluginConnectionRoutine]:
    if not connection_ids:
        return []
    rows = await session.scalars(
        select(PluginConnectionRoutine)
        .where(PluginConnectionRoutine.connection_id.in_(connection_ids))
        .order_by(PluginConnectionRoutine.created_at, PluginConnectionRoutine.routine)
    )
    return list(rows.all())


async def get_routine(session: AsyncSession, connection: PluginConnection, key: str) -> PluginConnectionRoutine:
    routine = await session.scalar(
        select(PluginConnectionRoutine).where(
            PluginConnectionRoutine.connection_id == connection.id, PluginConnectionRoutine.routine == key
        )
    )
    if routine is None:
        raise ConnectionNotFound
    return routine


async def _follow_routines(session: AsyncSession, connection: PluginConnection, integration: Integration) -> None:
    """One row per routine the integration declares now: new ones added, gone ones removed (not committed).

    A routine that stays keeps its state and history; its schedule follows the manifest.
    """
    existing = {r.routine: r for r in await list_routines(session, [connection.id])}
    declared = {r.key: r for r in integration.routines}
    for key, row in existing.items():
        if key not in declared:
            await session.delete(row)
    for key, routine in declared.items():
        row = existing.get(key)
        if row is None:
            session.add(
                PluginConnectionRoutine(
                    id=uuid.uuid4(), connection_id=connection.id, routine=key, every_minutes=routine.every_minutes,
                    timeout_seconds=routine.timeout_seconds, state={}, failures=0,
                )
            )
        else:
            row.every_minutes = routine.every_minutes
            row.timeout_seconds = routine.timeout_seconds


#: How a routine run ended: it ran (well or not), or it was skipped and why.
RoutineOutcome = Literal["ran", "busy", "needs_auth", "not_due"]


async def run_routine(
    session: AsyncSession,
    connection: PluginConnection,
    routine: PluginConnectionRoutine,
    *,
    scheduled: bool = False,
) -> RoutineOutcome:
    """Run one routine of a connection once and record how it went (ADR 0035).

    It holds the connection's lock, as a sync does: one call per connection at
    a time, so secrets one call refreshes are never overwritten by another.
    Under the lock it reads the connection and the routine again: one that
    needs signing in by then isn't run, and a `scheduled` run that another run
    already made isn't due anymore. "busy" when the lock is taken; the schedule
    tries again on its next round.

    The plugin's `routine` method gets the routine's key and its own state, and
    returns `{"state": ..., "secrets": ...}` as `sync` does (both optional).
    Signing in again (NeedsAuth) stops the whole connection; any other failure
    is the routine's own and backs it off.
    """
    lock = f"plugin-sync:{connection.id}"
    if not await _acquire(lock):
        return "busy"
    try:
        await session.refresh(connection)
        await session.refresh(routine)
        if connection.status == "needs_auth":
            return "needs_auth"
        if scheduled and not routine_due(routine, connection):
            return "not_due"
        params: dict[str, Any] = {
            "routine": routine.routine,
            "state": dict(routine.state or {}),
            "last_run_at": routine.last_run_at.isoformat() if routine.last_run_at else None,
        }
        try:
            record, manifest = await _installed(session, connection.workspace_id, connection.plugin_id)
            integration = _integration(manifest, connection.integration)
            result, secrets = await _call(
                session, connection, "routine", params, record, timeout=routine.timeout_seconds
            )
        except runner.PluginCallFailed as exc:
            message = f"{exc.kind}: {exc.message}"
            _routine_failed(routine, message)
            if exc.kind in ("NeedsAuth", "AuthError"):
                connection.status = "needs_auth"
                connection.last_error = message.replace("\x00", "")[:500]
                await _mirror_status(session, connection)
        except (runner.RunnerUnavailable, runner.RunnerRefused, PluginConnectionError) as exc:
            _routine_failed(routine, str(exc))
        else:
            if isinstance(result, dict):
                new_secrets = result.get("secrets")
                if isinstance(new_secrets, dict):
                    own = {k: v for k, v in new_secrets.items() if not str(k).startswith("__securo_")}
                    if own:
                        connection.secrets = vault.seal({**secrets, **own})
                if isinstance(result.get("state"), dict):
                    routine.state = result["state"]
            routine.last_run_at = _now()
            routine.last_error = None
            routine.failures = 0
            # A connection with no sync of its own is working once a routine ran.
            if connection.status == "pending" and integration.sync_minutes is None:
                connection.status = "active"
        await session.commit()
        return "ran"
    finally:
        await _release(lock)


async def _drop_routines(session: AsyncSession, connection: PluginConnection) -> None:
    """Its routines go with it (the foreign key cascades too; this keeps the session in step)."""
    for row in await list_routines(session, [connection.id]):
        await session.delete(row)


def _routine_failed(routine: PluginConnectionRoutine, message: str) -> None:
    routine.last_error = message.replace("\x00", "")[:500]
    routine.failures = (routine.failures or 0) + 1
    # Counts as an attempt, so the schedule backs off instead of retrying at once.
    routine.last_run_at = _now()


async def delete(session: AsyncSession, connection: PluginConnection) -> None:
    await _drop_bank(session, connection, keep_records=False)
    await _revoke_token(session, connection)
    await _drop_routines(session, connection)
    await notification_service.connection_changed(session, connection, removed=True)
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
        await _drop_bank(session, connection, keep_records=True)
        await _revoke_token(session, connection)
        await _drop_routines(session, connection)
        await notification_service.connection_changed(session, connection, removed=True)
        await session.delete(connection)
        count += 1
    return count


async def follow_manifest(session: AsyncSession, workspace_id: uuid.UUID, manifest: Manifest, *, lang: str) -> None:
    """After an update the owner agreed to, connections act with the new scopes (not committed).

    Each connection's token gets exactly the manifest's scopes. A plugin that
    starts declaring scopes gets a token per connection; one that stops has
    its tokens revoked. A connection whose integration starts providing
    records gets its bank connection; one whose integration stops is removed,
    as is one whose integration is gone.
    """
    scopes = [s.scope for s in manifest.api_scopes]
    rows = await session.scalars(
        select(PluginConnection).where(
            PluginConnection.workspace_id == workspace_id, PluginConnection.plugin_id == manifest.id
        )
    )
    integrations = {i.key: i for i in manifest.contributes.integrations}
    for connection in rows.all():
        integration = integrations.get(connection.integration)
        if integration is None or (connection.bank_connection_id is not None and not integration.is_provider):
            # The new version dropped this integration, or no longer syncs it
            # as a bank: nothing can run it as it was set up anymore.
            await _drop_bank(session, connection, keep_records=True)
            await _revoke_token(session, connection)
            await _drop_routines(session, connection)
            await notification_service.connection_changed(session, connection, removed=True)
            await session.delete(connection)
            continue
        if integration.is_provider and connection.bank_connection_id is None and connection.created_by is not None:
            await _add_bank(session, connection, localized(manifest.name, lang), connection.created_by)
        connection.sync_minutes = integration.sync_minutes
        await _follow_routines(session, connection, integration)
        token = await session.get(AccessToken, connection.access_token_id) if connection.access_token_id else None
        if token is not None and token.revoked_at is None and scopes:
            token.scopes = list(scopes)
            continue
        # Revoked before anything else, so a token never outlives the scopes it was for.
        await _revoke_token(session, connection)
        connection.access_token_id = None
        try:
            secrets = vault.unseal(connection.secrets)
        except vault.SealBroken:
            continue  # set up again anyway; nothing to hand a token to
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
