"""Catalog plugins in a workspace: list, plan, install, update and remove.

Installing and updating read the plugin from the catalog at the pinned
commit; the user confirms a plan for that exact commit, and applying is
refused if the catalog moved or the workspace changed since. Removing needs
no catalog at all, so a plugin can always be removed, even revoked or with
the catalog unreachable.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import index, sync
from app.plugins.manifest import Manifest
from app.services.rule_packs import built_in_packs

Action = Literal["install", "update", "remove"]


class PluginNotFound(Exception):
    pass


class PluginUnavailable(Exception):
    """The plugin can't be installed or updated here; the message says why."""


class PluginChanged(Exception):
    """The catalog moved to another commit since the plan was shown."""


@dataclass(frozen=True)
class CatalogItem:
    entry: index.IndexEntry
    manifest: Manifest | None
    error: str | None
    installed: WorkspacePlugin | None

    @property
    def compatible(self) -> bool:
        return self.manifest is not None and index.is_compatible(self.manifest)

    @property
    def update_available(self) -> bool:
        return self.installed is not None and self.installed.commit != self.entry.commit


@dataclass(frozen=True)
class PluginPlan:
    action: Action
    plugin_id: str
    commit: str | None
    """The commit the plan installs; None when removing."""
    sync: sync.SyncPlan


async def installed_plugins(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, WorkspacePlugin]:
    rows = await session.execute(select(WorkspacePlugin).where(WorkspacePlugin.workspace_id == workspace_id))
    return {row.plugin_id: row for row in rows.scalars().all()}


async def catalog(session: AsyncSession, workspace_id: uuid.UUID) -> list[CatalogItem]:
    """Every plugin in the catalog, with its manifest and where this workspace stands.

    A plugin whose manifest can't be read is still listed, with the reason, so
    it doesn't silently disappear.
    """
    installed = await installed_plugins(session, workspace_id)
    async with index.http_client() as client:
        entries = await index.fetch_index(client)

        async def load(entry: index.IndexEntry) -> CatalogItem:
            try:
                manifest, error = await index.fetch_manifest(client, entry), None
            except index.PluginIndexError as exc:
                manifest, error = None, str(exc)
            return CatalogItem(entry, manifest, error, installed.get(entry.id))

        return list(await asyncio.gather(*(load(entry) for entry in entries)))


async def plan(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, action: Action, *, lang: str
) -> PluginPlan:
    commit, manifest = await _target(session, workspace_id, plugin_id, action)
    plan = await sync.plan_sync(
        session, workspace_id, plugin_id, manifest, lang=lang, create_default_categories=True
    )
    return PluginPlan(action, plugin_id, commit, plan)


async def apply(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    plugin_id: str,
    action: Action,
    *,
    commit: str | None,
    token: str,
    lang: str,
) -> PluginPlan:
    """Carry out the confirmed plan; `PluginChanged` or `sync.SyncPlanChanged` if it moved."""
    target_commit, manifest = await _target(session, workspace_id, plugin_id, action)
    if target_commit != commit:
        raise PluginChanged
    result = await sync.apply_sync(
        session, workspace_id, user_id, plugin_id, manifest, token,
        lang=lang, create_default_categories=True,
    )
    record = (await installed_plugins(session, workspace_id)).get(plugin_id)
    if manifest is None or target_commit is None:  # removing
        if record is not None:
            await session.delete(record)
    else:
        if record is None:
            record = WorkspacePlugin(workspace_id=workspace_id, plugin_id=plugin_id, installed_by=user_id)
            session.add(record)
        record.name, record.version, record.commit = manifest.name, manifest.version, target_commit
    await session.commit()
    return PluginPlan(action, plugin_id, target_commit, result.plan)


async def _target(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, action: Action
) -> tuple[str | None, Manifest | None]:
    """The commit and manifest the action brings the workspace to."""
    if plugin_id in {pack.source for pack in built_in_packs().values()}:
        raise PluginNotFound  # built-in packs are managed from the rules page
    installed = (await installed_plugins(session, workspace_id)).get(plugin_id)
    if action == "remove":
        if installed is None and not await _has_items(session, workspace_id, plugin_id):
            raise PluginNotFound
        return None, None
    if action == "install" and installed is not None:
        raise PluginUnavailable("already installed")
    if action == "update" and installed is None:
        raise PluginUnavailable("not installed")
    async with index.http_client() as client:
        entry = next((e for e in await index.fetch_index(client) if e.id == plugin_id), None)
        if entry is None:
            raise PluginNotFound
        if entry.revoked:
            raise PluginUnavailable(f"revoked: {entry.revoked_reason or 'no reason given'}")
        manifest = await index.fetch_manifest(client, entry)
    if not index.is_compatible(manifest):
        raise PluginUnavailable(f"needs Securo {manifest.min_securo_version} or newer")
    if action == "update" and installed is not None and installed.commit == entry.commit:
        raise PluginUnavailable("already up to date")
    return entry.commit, manifest


async def _has_items(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> bool:
    for model in (Rule, Category, CategoryGroup):
        found = await session.scalar(
            select(model.id).where(model.workspace_id == workspace_id, model.source == plugin_id).limit(1)
        )
        if found is not None:
            return True
    return False

