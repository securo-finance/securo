"""Data plugins from the catalog, installed per workspace.

All of it answers 404 while PLUGINS_ENABLED is off. Reading the catalog
or a plan only needs to see the workspace; applying needs write access.
Access tokens can't reach these routes.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.feature_flags import feature_flag
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.plugins import service
from app.plugins.index import PluginIndexError
from app.plugins.sync import SyncPlanChanged
from app.schemas.plugin import (
    InstalledPluginRead,
    PluginApplyRequest,
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
        **entry.model_dump(exclude={"manifest", "manifest_sha256", "readme", "readme_sha256"}),
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
        **{b: items(b) for b in ("create", "update", "delete", "keep", "release", "existing", "unresolved")},
    )


async def _guarded(call):
    try:
        return await call
    except service.PluginNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
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
    return sorted(installed.values(), key=lambda p: p.plugin_id)


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
            commit=body.commit, token=body.token, lang=_lang(ctx),
        )
    )
    return _plan_read(plan)
