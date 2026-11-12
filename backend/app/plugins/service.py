"""Catalog plugins in a workspace: list, plan, install, update and remove.

Installing and updating read the plugin from the catalog at the pinned
commit; the user confirms a plan for that exact commit, and applying is
refused if the catalog moved or the workspace changed since. Removing needs
no catalog at all, so a plugin can always be removed, even revoked or with
the catalog unreachable.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.models.workspace import Workspace
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import index, sync
from app.plugins.manifest import Manifest
from app.services.category_defaults import DEFAULT_CATEGORIES, DEFAULT_GROUPS
from app.services.rule_packs import built_in_packs

logger = logging.getLogger(__name__)

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
    installed: WorkspacePlugin | None

    @property
    def update_available(self) -> bool:
        return self.installed is not None and self.installed.commit != self.entry.commit


@dataclass(frozen=True)
class PluginDetails:
    item: CatalogItem
    readme: str | None
    contents: dict[str, list[dict[str, Any]]] | None
    """What the plugin brings, references resolved; None when it can't be read here."""


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
    """Every plugin in the catalog and where this workspace stands with it: one request."""
    installed = await installed_plugins(session, workspace_id)
    async with index.http_client() as client:
        entries = await index.fetch_index(client)
    return [CatalogItem(entry, installed.get(entry.id)) for entry in entries]


async def details(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> PluginDetails:
    """One plugin, opened from the catalog: its README and everything it contains.

    Both are optional here. A README or manifest that can't be fetched or
    doesn't match its checksum leaves its part out (installing still refuses
    such a manifest) rather than hiding what the catalog does know.
    """
    installed = (await installed_plugins(session, workspace_id)).get(plugin_id)
    async with index.http_client() as client:
        entry = next((e for e in await index.fetch_index(client) if e.id == plugin_id), None)
        if entry is None:
            raise PluginNotFound
        readme = await _optional(index.fetch_readme(client, entry))
        manifest = await _optional(index.fetch_manifest(client, entry)) if entry.supported else None
    return PluginDetails(CatalogItem(entry, installed), readme, contents(manifest) if manifest else None)


async def _optional(call):
    try:
        return await call
    except index.PluginIndexError as exc:
        logger.warning("Plugin details incomplete: %s", exc)
        return None


def contents(manifest: Manifest) -> dict[str, list[dict[str, Any]]]:
    """The manifest's items, with every key it references resolved to a name."""
    own_groups = {g.key: g.name for g in manifest.contributes.category_groups}
    own_categories = {c.key: c.name for c in manifest.contributes.categories}

    def ref(key: str | None, own: dict, defaults: dict) -> dict[str, Any] | None:
        if key is None:
            return None
        if key in own:
            return {"key": key, "name": own[key], "default": False}
        return {"key": key, "name": defaults[key]["names"], "default": True}

    return {
        "category_groups": [g.model_dump() for g in manifest.contributes.category_groups],
        "categories": [
            c.model_dump(exclude={"group"}) | {"group": ref(c.group, own_groups, DEFAULT_GROUPS)}
            for c in manifest.contributes.categories
        ],
        "rules": [
            {
                **rule.template(),
                "actions": [
                    action.model_dump(exclude_none=True)
                    | {"category": ref(str(action.value), own_categories, DEFAULT_CATEGORIES)
                       if action.op == "set_category" else None}
                    for action in rule.actions
                ],
            }
            for rule in manifest.contributes.rules
        ],
    }


async def plan(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, action: Action, *, lang: str
) -> PluginPlan:
    commit, manifest, checksum = await _target(session, workspace_id, plugin_id, action)
    plan = await sync.plan_sync(
        session, workspace_id, plugin_id, manifest, lang=lang, create_default_categories=True, fingerprint=checksum
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
    target_commit, manifest, checksum = await _target(session, workspace_id, plugin_id, action)
    if target_commit != commit:
        raise PluginChanged
    # One plugin change per workspace at a time: two confirmations of the same
    # first install would otherwise both find nothing to lock and both create.
    # Taken after the catalog fetch, so no network wait holds it; the plan is
    # worked out again under it, and the second one no longer matches its token.
    await session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    result = await sync.apply_sync(
        session, workspace_id, user_id, plugin_id, manifest, token,
        lang=lang, create_default_categories=True, fingerprint=checksum,
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
) -> tuple[str | None, Manifest | None, str | None]:
    """The commit, manifest and manifest checksum the action brings the workspace to."""
    if plugin_id in {pack.source for pack in built_in_packs().values()}:
        raise PluginNotFound  # built-in packs are managed from the rules page
    installed = (await installed_plugins(session, workspace_id)).get(plugin_id)
    if action == "remove":
        if installed is None and not await _has_items(session, workspace_id, plugin_id):
            raise PluginNotFound
        return None, None, None
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
        if not entry.compatible:
            raise PluginUnavailable(f"needs Securo {entry.min_securo_version} or newer")
        if not entry.supported:
            raise PluginUnavailable("contributes something this Securo version can't apply")
        manifest = await index.fetch_manifest(client, entry)
    # The index's copy of the minimum is a summary; the verified manifest decides.
    if not index.is_compatible(manifest.min_securo_version):
        raise PluginUnavailable(f"needs Securo {manifest.min_securo_version} or newer")
    if action == "update" and installed is not None and installed.commit == entry.commit:
        raise PluginUnavailable("already up to date")
    return entry.commit, manifest, entry.manifest_sha256


async def _has_items(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> bool:
    for model in (Rule, Category, CategoryGroup):
        found = await session.scalar(
            select(model.id).where(model.workspace_id == workspace_id, model.source == plugin_id).limit(1)
        )
        if found is not None:
            return True
    return False

