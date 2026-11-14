"""Plugins from the catalog, installed per workspace, and their connections.

All of it answers 404 while PLUGINS_ENABLED is off. Reading the catalog
or a plan only needs to see the workspace; applying needs write access,
and for a plugin with code, the owner. Connections need write access.
Access tokens can't reach these routes.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.feature_flags import feature_flag
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.plugins import connections, runner, service
from app.plugins.index import PluginIndexError
from app.plugins.manifest import parse_manifest
from app.services.access_token_service import AccessTokenError
from app.plugins.sync import SyncPlanChanged
from app.schemas.plugin import (
    InstalledPluginRead,
    PluginApplyRequest,
    PluginAuthorizeRead,
    PluginConnectionCreate,
    PluginConnectionCreated,
    PluginConnectionRead,
    PluginOAuthCallback,
    PluginCatalogRead,
    PluginDetailRead,
    PluginItemRead,
    PluginPlanRead,
)


def _plugins_enabled() -> None:
    if not feature_flag("PLUGINS_ENABLED"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugins are not enabled")


router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(_plugins_enabled)])


def _lang(ctx: WorkspaceContext) -> str:
    """The language new categories are named in: the user's, else the workspace's."""
    return (ctx.user.preferences or {}).get("language") or ctx.workspace.locale or "en"


def _catalog_read(item: service.CatalogItem) -> PluginCatalogRead:
    entry = item.entry
    return PluginCatalogRead(
        **entry.model_dump(exclude={"manifest", "manifest_sha256", "readme", "readme_sha256", "archive", "archive_sha256"}),
        owner=entry.owner,
        compatible=entry.compatible,
        supported=entry.supported,
        installed_version=item.installed.version if item.installed else None,
        update_available=item.update_available,
    )


def _plan_read(plan: service.PluginPlan) -> PluginPlanRead:
    def items(bucket: str) -> list[PluginItemRead]:
        return [PluginItemRead(kind=i.kind, key=i.key, name=i.name) for i in getattr(plan.sync, bucket)]

    return PluginPlanRead(
        action=plan.action,
        plugin_id=plan.plugin_id,
        version=plan.sync.version,
        commit=plan.commit,
        token=plan.sync.token,
        default_categories=list(plan.sync.default_categories),
        code=plan.code.__dict__ if plan.code else None,
        themes=plan.themes,
        **{b: items(b) for b in ("create", "update", "delete", "keep", "release", "existing", "unresolved")},
    )


async def _guarded(call):
    try:
        return await call
    except service.PluginNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    except service.PluginForbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can add, update or remove a plugin with code",
        )
    except service.PluginUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Plugin unavailable: {exc}")
    except (service.PluginChanged, SyncPlanChanged):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The plugin or this workspace changed since the preview. Review it again.",
        )
    except PluginIndexError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Plugin catalog unavailable: {exc}")


@router.get("", response_model=list[InstalledPluginRead])
async def list_installed(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Plugins installed in this workspace. Reads only the database, never the catalog."""
    installed = await service.installed_plugins(session, ctx.workspace.id)
    return [_installed_read(p) for p in sorted(installed.values(), key=lambda p: p.plugin_id)]


def _installed_read(record) -> InstalledPluginRead:
    manifest = parse_manifest(record.manifest) if record.manifest else None
    return InstalledPluginRead(
        plugin_id=record.plugin_id,
        name=record.name,
        version=record.version,
        commit=record.commit,
        installed_at=record.installed_at,
        integrations=[i.model_dump() for i in manifest.contributes.integrations] if manifest else [],
        network=[h.model_dump() for h in manifest.network] if manifest else [],
        api_scopes=[a.model_dump() for a in manifest.api_scopes] if manifest else [],
        redirect_uri=connections.redirect_uri()
        if manifest and any(i.auth == "oauth" for i in manifest.contributes.integrations)
        else None,
        themes=[t.model_dump() for t in manifest.contributes.themes] if manifest else [],
    )


# Connections ---------------------------------------------------------------


def _connection_read(connection) -> PluginConnectionRead:
    try:
        secret_fields = sorted(k for k in connections.vault.unseal(connection.secrets) if not k.startswith("__securo_"))
    except connections.vault.SealBroken:
        secret_fields = []
    return PluginConnectionRead.model_validate(connection).model_copy(update={"secret_fields": secret_fields})


async def _connection_guarded(call):
    try:
        return await call
    except connections.ConnectionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    except (connections.PluginConnectionError, AccessTokenError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    except runner.PluginCallFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The plugin failed: {exc.message}")
    except (runner.RunnerUnavailable, runner.RunnerRefused) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Plugin runner: {exc}")


@router.get("/connections", response_model=list[PluginConnectionRead])
async def list_connections(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return [_connection_read(c) for c in await connections.list_connections(session, ctx.workspace.id)]


@router.post("/connections", response_model=PluginConnectionCreated, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: PluginConnectionCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Set up a connection. Sign-in integrations come back with the link to open."""
    connection, link = await _connection_guarded(
        connections.create(
            session,
            workspace_id=ctx.workspace.id,
            user_id=ctx.user_id,
            can_write=ctx.can_write,
            plugin_id=body.plugin_id,
            integration_key=body.integration,
            name=body.name,
            values=body.values,
            lang=_lang(ctx),
        )
    )
    if link is None:
        _queue_sync(connection.id)
    return PluginConnectionCreated(connection=_connection_read(connection), authorize_url=link)


@router.post("/connections/{connection_id}/authorize", response_model=PluginAuthorizeRead)
async def authorize_connection(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """A fresh sign-in link, to finish or redo the sign-in."""
    connection = await _connection_guarded(connections.get_connection(session, ctx.workspace.id, connection_id))
    link = await _connection_guarded(connections.authorize_url(session, connection, user_id=ctx.user_id))
    return PluginAuthorizeRead(authorize_url=link, redirect_uri=connections.redirect_uri())


@router.post("/oauth/callback", response_model=PluginConnectionRead)
async def oauth_callback(
    body: PluginOAuthCallback,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The service sent the browser back: the plugin trades the code for a token."""
    connection = await _connection_guarded(
        connections.finish_oauth(session, workspace_id=ctx.workspace.id, user_id=ctx.user_id, code=body.code, state=body.state)
    )
    _queue_sync(connection.id)
    return _connection_read(connection)


@router.post("/connections/{connection_id}/sync", response_model=PluginConnectionRead)
async def sync_connection(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Sync now, and wait for it (the plugin's timeout bounds the wait)."""
    connection = await _connection_guarded(connections.get_connection(session, ctx.workspace.id, connection_id))
    return _connection_read(await connections.sync(session, connection))


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Remove the connection, its secrets and its access token."""
    connection = await _connection_guarded(connections.get_connection(session, ctx.workspace.id, connection_id))
    await connections.delete(session, connection)


def _queue_sync(connection_id: uuid.UUID) -> None:
    from app.tasks.plugin_tasks import sync_plugin_connection

    try:
        sync_plugin_connection.delay(str(connection_id))
    except Exception:  # noqa: BLE001 - the schedule picks it up anyway
        pass


@router.get("/catalog", response_model=list[PluginCatalogRead])
async def get_catalog(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The plugin catalog: one request to the index, now, never in the background."""
    items = await _guarded(service.catalog(session, ctx.workspace.id))
    return [_catalog_read(item) for item in items]


@router.get("/{owner}/{name}", response_model=PluginDetailRead)
async def get_details(
    owner: str,
    name: str,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """One plugin from the catalog: its README and what it contains, fetched when it's opened."""
    details = await _guarded(service.details(session, ctx.workspace.id, f"{owner}/{name}"))
    return PluginDetailRead(plugin=_catalog_read(details.item), readme=details.readme, contents=details.contents)


@router.get("/{owner}/{name}/plan", response_model=PluginPlanRead)
async def get_plan(
    owner: str,
    name: str,
    action: service.Action = Query(...),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """What installing, updating or removing the plugin would do here, and the token to confirm it."""
    plan = await _guarded(service.plan(session, ctx.workspace.id, f"{owner}/{name}", action, lang=_lang(ctx)))
    return _plan_read(plan)


@router.post("/{owner}/{name}/apply", response_model=PluginPlanRead)
async def apply_plan(
    owner: str,
    name: str,
    body: PluginApplyRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Carry out a plan from `/plan`. 409 and nothing changes if it moved since."""
    plan = await _guarded(
        service.apply(
            session, ctx.workspace.id, ctx.user_id, f"{owner}/{name}", body.action,
            commit=body.commit, token=body.token, lang=_lang(ctx), is_owner=ctx.is_owner,
        )
    )
    return _plan_read(plan)
