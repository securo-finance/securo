"""Catalog plugins in a workspace: list, plan, install, update and remove.

Installing and updating read the plugin from the catalog at the pinned
commit; the user confirms a plan for that exact commit, and applying is
refused if the catalog moved or the workspace changed since. Removing needs
no catalog at all, so a plugin can always be removed, even revoked or with
the catalog unreachable.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.models.workspace import Workspace
from app.models.plugin_connection import PluginConnection
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import connections, index, runner, sync
from app.plugins.manifest import Manifest, parse_manifest
from app.services.category_defaults import DEFAULT_CATEGORIES, DEFAULT_GROUPS
from app.services.rule_packs import built_in_packs

logger = logging.getLogger(__name__)

Action = Literal["install", "update", "remove"]


class PluginNotFound(Exception):
    pass


class PluginUnavailable(Exception):
    """The plugin can't be installed or updated here; the message says why."""


class PluginForbidden(Exception):
    """Plugins with code run on the server: only the workspace's owner adds, updates or removes them."""


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
class CodeConsent:
    """What a plugin with code is allowed to do: what the owner agrees to."""

    network: list[dict[str, Any]]
    api_scopes: list[dict[str, Any]]
    integrations: list[dict[str, Any]]
    new_hosts: list[str]
    """On an update: hosts the current version couldn't reach."""
    new_scopes: list[str]
    connections: int
    """Connections in this workspace (removed along with the plugin)."""
    drops_code: bool = False
    """An update to a version without code: its connections go, as on removal."""


@dataclass(frozen=True)
class PluginPlan:
    action: Action
    plugin_id: str
    commit: str | None
    """The commit the plan installs; None when removing."""
    sync: sync.SyncPlan
    code: CodeConsent | None = None
    themes: dict[str, list[dict[str, Any]]] | None = None
    """Themes the action adds to the library (`added`) and takes out of it (`removed`)."""


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
        "integrations": [i.model_dump() for i in manifest.contributes.integrations],
        "themes": [t.model_dump() for t in manifest.contributes.themes],
        "network": [h.model_dump() for h in manifest.network],
        "api_scopes": [a.model_dump() for a in manifest.api_scopes],
    }


async def plan(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, action: Action, *, lang: str
) -> PluginPlan:
    commit, manifest, checksum, _ = await _target(session, workspace_id, plugin_id, action)
    fingerprint = await _fingerprint(session, workspace_id, plugin_id, checksum)
    plan = await sync.plan_sync(
        session, workspace_id, plugin_id, manifest, lang=lang, create_default_categories=True, fingerprint=fingerprint
    )
    return PluginPlan(
        action, plugin_id, commit, plan,
        await _consent(session, workspace_id, plugin_id, manifest),
        await _themes(session, workspace_id, plugin_id, manifest),
    )


async def _themes(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, manifest: Manifest | None
) -> dict[str, list[dict[str, Any]]] | None:
    """Themes the action adds to the library and takes out of it."""
    current = _installed_manifest((await installed_plugins(session, workspace_id)).get(plugin_id))
    before = {t.key: t for t in current.contributes.themes} if current else {}
    after = {t.key: t for t in manifest.contributes.themes} if manifest else {}
    added = [{"key": k, "name": t.name} for k, t in after.items() if k not in before]
    removed = [{"key": k, "name": t.name} for k, t in before.items() if k not in after]
    if not added and not removed:
        return None
    return {"added": added, "removed": removed}


def theme_id(plugin_id: str, key: str) -> str:
    """How the library names a plugin's theme, as saved in `users.theme`."""
    return f"plugin:{plugin_id}:{key}"


async def _release_themes(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, keys: set[str]) -> None:
    """Members using a theme that leaves the library go back to Securo's own (not committed).

    Unless the same plugin is still installed in another workspace of theirs,
    where the theme keeps showing.
    """
    if not keys:
        return
    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    ids = {theme_id(plugin_id, key) for key in keys}
    # Ids first, then rows by id: users.id and workspace_members.user_id
    # use different column types, so they're compared as values, not joined.
    member_ids = list((await session.scalars(
        select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id)
    )).all())
    users = (await session.scalars(select(User).where(User.id.in_(member_ids), User.theme.in_(ids)))).all()
    for user in users:
        theirs = list((await session.scalars(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id != workspace_id
            )
        )).all())
        elsewhere = await session.scalar(
            select(WorkspacePlugin.id)
            .where(WorkspacePlugin.plugin_id == plugin_id, WorkspacePlugin.workspace_id.in_(theirs))
            .limit(1)
        ) if theirs else None
        if elsewhere is None:
            user.theme = None


async def _fingerprint(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, checksum: str | None) -> str | None:
    """The manifest's checksum, plus the connections a removal or update would touch.

    Bound into the confirmation token, so a connection added after the
    preview is never deleted by confirming it.
    """
    ids = sorted(
        str(i)
        for i in (
            await session.scalars(
                select(PluginConnection.id).where(
                    PluginConnection.workspace_id == workspace_id, PluginConnection.plugin_id == plugin_id
                )
            )
        ).all()
    )
    if not ids:
        return checksum
    digest = hashlib.sha256(",".join(ids).encode()).hexdigest()
    return f"{checksum or ''}|{digest}"


def _installed_manifest(record: WorkspacePlugin | None) -> Manifest | None:
    return parse_manifest(record.manifest) if record is not None and record.manifest else None


async def _consent(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, manifest: Manifest | None
) -> CodeConsent | None:
    record = (await installed_plugins(session, workspace_id)).get(plugin_id)
    current = _installed_manifest(record)
    drops_code = bool(current and current.has_code and manifest is not None and not manifest.has_code)
    # Removing, or updating to a version without code, shows what is taken away.
    shown = current if drops_code else (manifest or current)
    if shown is None or not shown.has_code:
        return None
    count = await session.scalar(
        select(func.count()).select_from(PluginConnection).where(
            PluginConnection.workspace_id == workspace_id,
            PluginConnection.plugin_id == plugin_id,
        )
    )
    old_hosts = {h.host for h in current.network} if current and manifest else set()
    old_scopes = {a.scope for a in current.api_scopes} if current and manifest else set()
    return CodeConsent(
        network=[h.model_dump() for h in shown.network],
        api_scopes=[a.model_dump() for a in shown.api_scopes],
        integrations=[{"key": i.key, "name": i.name, "auth": i.auth} for i in shown.contributes.integrations],
        new_hosts=sorted({h.host for h in shown.network} - old_hosts) if current and manifest else [],
        new_scopes=sorted({a.scope for a in shown.api_scopes} - old_scopes) if current and manifest else [],
        connections=int(count or 0),
        drops_code=drops_code,
    )


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
    is_owner: bool = False,
) -> PluginPlan:
    """Carry out the confirmed plan; `PluginChanged` or `sync.SyncPlanChanged` if it moved."""
    target_commit, manifest, checksum, entry = await _target(session, workspace_id, plugin_id, action)
    if target_commit != commit:
        raise PluginChanged
    previous = (await installed_plugins(session, workspace_id)).get(plugin_id)
    previous_commit = previous.commit if previous is not None else None
    current = _installed_manifest(previous)
    has_code = bool((manifest and manifest.has_code) or (current and current.has_code))
    if has_code and not is_owner:
        raise PluginForbidden
    # The code goes on the runner before anything changes here, so check the
    # confirmation first: a stale preview mustn't install anything.
    fingerprint = await _fingerprint(session, workspace_id, plugin_id, checksum)
    preview = await sync.plan_sync(
        session, workspace_id, plugin_id, manifest, lang=lang, create_default_categories=True, fingerprint=fingerprint
    )
    if preview.token != token:
        raise sync.SyncPlanChanged
    installed_now = False
    if manifest is not None and manifest.has_code and entry is not None:
        # If the runner can't take it, nothing changes here.
        installed_now = await _install_code(entry, manifest)
    try:
        consent = await _consent(session, workspace_id, plugin_id, manifest)
        # One plugin change per workspace at a time: two confirmations of the same
        # first install would otherwise both find nothing to lock and both create.
        # Taken after the catalog fetch, so no network wait holds it; the plan is
        # worked out again under it, and the second one no longer matches its token.
        await session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
        # What was read before the lock decided the owner check and what to
        # release afterwards: if another change landed meanwhile, start over.
        locked = (await installed_plugins(session, workspace_id)).get(plugin_id)
        if (locked.commit if locked is not None else None) != previous_commit:
            raise PluginChanged
        result = await sync.apply_sync(
            session, workspace_id, user_id, plugin_id, manifest, token,
            lang=lang, create_default_categories=True,
            fingerprint=await _fingerprint(session, workspace_id, plugin_id, checksum),
        )
        record = (await installed_plugins(session, workspace_id)).get(plugin_id)
        gone_themes = {t.key for t in current.contributes.themes} - (
            {t.key for t in manifest.contributes.themes} if manifest else set()
        ) if current else set()
        await _release_themes(session, workspace_id, plugin_id, gone_themes)
        drops_code = current is not None and current.has_code and not (manifest and manifest.has_code)
        if drops_code:
            # A version without code (or removing): its connections and their tokens go too.
            await connections.delete_all(session, workspace_id, plugin_id)
        if manifest is None or target_commit is None:  # removing
            if record is not None:
                await session.delete(record)
        else:
            if record is None:
                record = WorkspacePlugin(workspace_id=workspace_id, plugin_id=plugin_id, installed_by=user_id)
                session.add(record)
            record.name, record.version, record.commit = manifest.name, manifest.version, target_commit
            # Kept for every plugin: connections need the integrations, the
            # library the themes, and neither should depend on the catalog.
            record.manifest = manifest.model_dump(mode="json")
            if action == "update" and manifest.has_code:
                await connections.follow_manifest(session, workspace_id, manifest, lang=lang)
        await session.commit()
    except BaseException:
        if installed_now and target_commit is not None:
            await session.rollback()
            await _release_code(session, plugin_id, target_commit)
        raise
    if current is not None and current.has_code and previous_commit and previous_commit != target_commit:
        await _release_code(session, plugin_id, previous_commit)
    return PluginPlan(action, plugin_id, target_commit, result.plan, consent, None)


async def _install_code(entry: index.IndexEntry, manifest: Manifest) -> bool:
    """Put the pinned commit on the runner; False when it was there already."""
    if not runner.configured():
        raise PluginUnavailable("plugins with code need the plugin runner (the `plugins` profile)")
    try:
        return await install_on_runner(entry, manifest)
    except (runner.RunnerUnavailable, runner.RunnerRefused) as exc:
        raise PluginUnavailable(str(exc)) from exc


async def install_on_runner(entry: index.IndexEntry, manifest: Manifest) -> bool:
    assert manifest.runtime is not None
    async with runner.RunnerClient() as client:
        if any(p.id == entry.id and p.commit == entry.commit for p in await client.installed()):
            return False
        async with index.http_client() as http:
            archive, sha256 = await index.fetch_archive(http, entry)
        await client.install(
            entry.id,
            commit=entry.commit,
            sha256=sha256,
            archive=archive,
            entrypoint=manifest.runtime.entrypoint,
            lockfile=manifest.runtime.lockfile,
            hosts=[h.host for h in manifest.network],
        )
    return True


async def _release_code(session: AsyncSession, plugin_id: str, commit: str) -> None:
    """Take a commit off the runner once no workspace runs it."""
    still_used = await session.scalar(
        select(WorkspacePlugin.id)
        .where(WorkspacePlugin.plugin_id == plugin_id, WorkspacePlugin.commit == commit)
        .limit(1)
    )
    if still_used is not None or not runner.configured():
        return
    try:
        async with runner.RunnerClient() as client:
            await client.remove(plugin_id, commit)
    except (runner.RunnerUnavailable, runner.RunnerRefused) as exc:
        logger.warning("Couldn't remove %s at %s from the plugin runner: %s", plugin_id, commit, exc)


async def _target(
    session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str, action: Action
) -> tuple[str | None, Manifest | None, str | None, index.IndexEntry | None]:
    """The commit, manifest, manifest checksum and catalog entry the action brings the workspace to."""
    if plugin_id in {pack.source for pack in built_in_packs().values()}:
        raise PluginNotFound  # built-in packs are managed from the rules page
    installed = (await installed_plugins(session, workspace_id)).get(plugin_id)
    if action == "remove":
        if installed is None and not await _has_items(session, workspace_id, plugin_id):
            raise PluginNotFound
        return None, None, None, None
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
    return entry.commit, manifest, entry.manifest_sha256, entry


async def _has_items(session: AsyncSession, workspace_id: uuid.UUID, plugin_id: str) -> bool:
    for model in (Rule, Category, CategoryGroup):
        found = await session.scalar(
            select(model.id).where(model.workspace_id == workspace_id, model.source == plugin_id).limit(1)
        )
        if found is not None:
            return True
    return False

