"""Sync a workspace to a data plugin: install, update and remove are one operation.

Given a plugin id and its manifest (or none, to remove it), `plan_sync` works
out what changes in the workspace, item by item, and `apply_sync` carries out
exactly that plan, refusing if anything moved since it was shown:

- an item the plugin has and the workspace doesn't is created, unless the
  workspace already has one by that name, which is left alone;
- an item the plugin created and the user never edited follows the plugin:
  it is updated to the new content, or deleted once the plugin drops it;
- an item the user edited is theirs: kept as is while the plugin still has
  it, and handed over (its origin cleared) once the plugin drops it;
- a category still in use, or a group with categories left in it, is handed
  over instead of deleted. Removing a plugin never rewrites history:
  transactions keep the categories they have.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.plugins.manifest import Manifest, localized
from app.plugins.provenance import (
    category_content_hash,
    group_content_hash,
    is_untouched,
    row_hash,
    rule_content_hash,
)
from app.services.category_defaults import DEFAULT_GROUPS, name_variants
from app.services.category_service import get_category_usage
from app.services.rule_service import (
    ensure_default_categories,
    existing_rule_names,
    resolve_default_category_keys,
    validate_rule_definition,
)

GROUPS = "category_groups"
CATEGORIES = "categories"
RULES = "rules"
KINDS = (GROUPS, CATEGORIES, RULES)


@dataclass(frozen=True)
class Item:
    kind: str
    key: str
    name: str
    id: uuid.UUID | None = None


@dataclass(frozen=True)
class SyncPlan:
    """What syncing the workspace to the plugin does, item by item."""

    source: str
    version: str | None
    """The manifest's version; None when the plugin is being removed."""
    create: tuple[Item, ...] = ()
    update: tuple[Item, ...] = ()
    delete: tuple[Item, ...] = ()
    keep: tuple[Item, ...] = ()
    """Edited by the user: left as they are."""
    release: tuple[Item, ...] = ()
    """No longer the plugin's (edited, or still in use): handed over to the user."""
    existing: tuple[Item, ...] = ()
    """The workspace already has one by that name: left alone."""
    unresolved: tuple[Item, ...] = ()
    """Rules whose default category the workspace doesn't have."""
    default_categories: tuple[str, ...] = ()
    """Default category keys created first so rules can use them."""
    fingerprint: str | None = None
    """The exact content planned from (a manifest checksum), so a changed copy is refused."""

    @property
    def token(self) -> str:
        """Identifies this exact plan; applying is refused under another."""
        def refs(items: tuple[Item, ...]) -> list[str]:
            return sorted(f"{i.kind}:{i.key}:{i.id or ''}" for i in items)

        payload = {
            "source": self.source,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "default_categories": sorted(self.default_categories),
            **{
                bucket: refs(getattr(self, bucket))
                for bucket in ("create", "update", "delete", "keep", "release", "existing", "unresolved")
            },
        }
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    def of(self, bucket: str, kind: str) -> list[Item]:
        return [item for item in getattr(self, bucket) if item.kind == kind]


@dataclass
class SyncResult:
    plan: SyncPlan
    created: dict[str, list[Any]] = field(default_factory=dict)


class SyncPlanChanged(Exception):
    """The workspace changed since the plan was shown; nothing was applied."""


# --- Planning ---------------------------------------------------------------------


@dataclass
class _State:
    """What planning learned that applying reuses."""

    rows: dict[str, dict[str, Any]]
    """The plugin's rows in the workspace, by kind then key."""
    adopted: dict[str, dict[str, uuid.UUID]]
    """Keys the workspace already has by name: key -> that row's id."""


async def plan_sync(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    source: str,
    manifest: Manifest | None,
    *,
    lang: str = "en",
    create_default_categories: bool = False,
    fingerprint: str | None = None,
) -> SyncPlan:
    plan, _ = await _plan(
        session, workspace_id, source, manifest, lang, create_default_categories, lock=False, fingerprint=fingerprint
    )
    return plan


async def _plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    source: str,
    manifest: Manifest | None,
    lang: str,
    create_defaults: bool,
    *,
    lock: bool,
    fingerprint: str | None = None,
) -> tuple[SyncPlan, _State]:
    rows = {
        GROUPS: await _rows(session, CategoryGroup, workspace_id, source, lock),
        CATEGORIES: await _rows(session, Category, workspace_id, source, lock),
        RULES: await _rows(session, Rule, workspace_id, source, lock),
    }
    group_names = await _group_names(session, workspace_id, source)
    category_rows = (
        await session.execute(
            select(Category.id, Category.name, Category.source).where(Category.workspace_id == workspace_id)
        )
    ).all()
    category_names = {name: str(cid) for cid, name, src in category_rows}
    foreign_category_names = {name: cid for cid, name, src in category_rows if src != source}
    rule_names = await existing_rule_names(session, workspace_id)
    own_rule_names = {row.name for row in rows[RULES].values()}

    state = _State(rows, {GROUPS: {}, CATEGORIES: {}})
    buckets: dict[str, list[Item]] = {
        b: [] for b in ("create", "update", "delete", "keep", "release", "existing", "unresolved")
    }
    defaults_needed: set[str] = set()
    contributes = manifest.contributes if manifest else None
    target_groups = {g.key: g for g in contributes.category_groups} if contributes else {}
    target_categories = {c.key: c for c in contributes.categories} if contributes else {}
    target_rules = {r.key: r for r in contributes.rules} if contributes else {}
    default_ids = resolve_default_category_keys(category_names)
    planned_refs: set[str] = set()
    """Category ids the plugin's rules will point at after this sync."""

    # Groups
    for key, group in target_groups.items():
        name = localized(group.name, lang)
        row = rows[GROUPS].get(key)
        if row is not None:
            desired = group_content_hash(name=name, icon=group.icon, color=group.color)
            _place(buckets, GROUPS, key, row, desired)
        elif name in group_names:
            state.adopted[GROUPS][key] = group_names[name]
            buckets["existing"].append(Item(GROUPS, key, name, group_names[name]))
        else:
            buckets["create"].append(Item(GROUPS, key, name))

    def group_ref(key: str | None) -> uuid.UUID | str | None:
        """The id a category's group resolves to now; a marker if it's yet to be created."""
        if key is None:
            return None
        if key in target_groups:
            if key in rows[GROUPS]:
                return rows[GROUPS][key].id
            return state.adopted[GROUPS].get(key) or f"new:{key}"
        return _default_group_id(key, group_names)

    # Categories
    for key, category in target_categories.items():
        name = localized(category.name, lang)
        row = rows[CATEGORIES].get(key)
        if row is not None:
            desired = category_content_hash(
                name=name, icon=category.icon, color=category.color,
                group_id=group_ref(category.group), treat_as_transfer=category.treat_as_transfer,
            )
            _place(buckets, CATEGORIES, key, row, desired)
        elif name in foreign_category_names:
            state.adopted[CATEGORIES][key] = foreign_category_names[name]
            buckets["existing"].append(Item(CATEGORIES, key, name, foreign_category_names[name]))
        else:
            buckets["create"].append(Item(CATEGORIES, key, name))

    def category_ref(key: str, *, for_new_rule: bool) -> str | None:
        """The id a rule's category resolves to now, a marker, or None if missing.

        A missing default category is created only for a rule being created:
        a rule already in the workspace is never a reason to bring back a
        default category the user deleted.
        """
        if key in target_categories:
            if key in rows[CATEGORIES]:
                return str(rows[CATEGORIES][key].id)
            adopted = state.adopted[CATEGORIES].get(key)
            return str(adopted) if adopted else f"new:{key}"
        if key in default_ids:
            return default_ids[key]
        if create_defaults and for_new_rule:
            defaults_needed.add(key)
            return f"default:{key}"
        return None

    def existing_ref(key: str) -> str | None:
        return category_ref(key, for_new_rule=False)

    # Rules
    taken_rule_names = rule_names - own_rule_names
    for key, rule in target_rules.items():
        template = rule.template()
        row = rows[RULES].get(key)
        if row is not None:
            refs = {existing_ref(k) for k in rule.category_keys()}
            if None in refs:
                continue  # its category is gone from the workspace: leave the rule as it is
            if is_untouched(row) and rule.name != row.name and rule.name in taken_rule_names:
                # Renamed to a name another rule has: leave this one as it is.
                buckets["existing"].append(Item(RULES, key, rule.name, row.id))
                planned_refs |= _ids(row.actions or [])
                continue
            actions = _resolve_actions(template["actions"], existing_ref)
            desired = rule_content_hash(
                name=rule.name, conditions_op=rule.conditions_op, conditions=template["conditions"],
                actions=actions, priority=rule.priority,
            )
            _place(buckets, RULES, key, row, desired)
            planned_refs |= _ids(actions if is_untouched(row) else row.actions or [])
        elif rule.name in taken_rule_names:
            buckets["existing"].append(Item(RULES, key, rule.name))
        elif None in (refs := {category_ref(k, for_new_rule=True) for k in rule.category_keys()}):
            buckets["unresolved"].append(Item(RULES, key, rule.name))
        else:
            buckets["create"].append(Item(RULES, key, rule.name))
            planned_refs |= {ref for ref in refs if ref}
    for key, row in rows[RULES].items():
        if key not in target_rules:
            _drop(buckets, RULES, key, row)

    # Categories and groups the plugin dropped go only when nothing holds on to them.
    deleted_rules = {item.id for item in buckets["delete"] if item.kind == RULES}
    for key, row in rows[CATEGORIES].items():
        if key in target_categories:
            continue
        if not is_untouched(row):
            buckets["release"].append(Item(CATEGORIES, key, row.name, row.id))
            continue
        usage = await get_category_usage(session, workspace_id, row.id)
        held = usage.transactions or usage.budgets or usage.recurring_transactions or any(
            r.id not in deleted_rules for r in usage.rules
        ) or str(row.id) in planned_refs  # a rule this sync creates or updates points at it
        bucket = "release" if held else "delete"
        buckets[bucket].append(Item(CATEGORIES, key, row.name, row.id))
    deleted_categories = {item.id for item in buckets["delete"] if item.kind == CATEGORIES}
    for key, row in rows[GROUPS].items():
        if key in target_groups:
            continue
        members = (
            await session.execute(select(Category.id).where(Category.group_id == row.id))
        ).scalars().all()
        empty = all(member in deleted_categories for member in members)
        bucket = "delete" if is_untouched(row) and empty else "release"
        buckets[bucket].append(Item(GROUPS, key, row.name, row.id))

    plan = SyncPlan(
        source=source,
        version=manifest.version if manifest else None,
        default_categories=tuple(sorted(defaults_needed)),
        fingerprint=fingerprint,
        **{bucket: tuple(items) for bucket, items in buckets.items()},
    )
    return plan, state


def _place(buckets: dict[str, list[Item]], kind: str, key: str, row: Any, desired: str) -> None:
    """An item the plugin still has and already created: keep, update or leave."""
    if not is_untouched(row):
        buckets["keep"].append(Item(kind, key, row.name, row.id))
    elif row.source_hash != desired:
        buckets["update"].append(Item(kind, key, row.name, row.id))


def _drop(buckets: dict[str, list[Item]], kind: str, key: str, row: Any) -> None:
    bucket = "delete" if is_untouched(row) else "release"
    buckets[bucket].append(Item(kind, key, row.name, row.id))


async def _rows(session: AsyncSession, model: Any, workspace_id: uuid.UUID, source: str, lock: bool) -> dict[str, Any]:
    query = select(model).where(model.workspace_id == workspace_id, model.source == source)
    if lock:
        # Re-read as the database has them now and hold them until commit, so an
        # edit saved meanwhile can't slip between the check and the change.
        query = query.with_for_update().execution_options(populate_existing=True)
    return {row.source_key: row for row in (await session.execute(query)).scalars().all()}


async def _group_names(session: AsyncSession, workspace_id: uuid.UUID, source: str) -> dict[str, uuid.UUID]:
    """The workspace's groups that aren't this plugin's, by name: for default group keys."""
    rows = await session.execute(
        select(CategoryGroup.id, CategoryGroup.name, CategoryGroup.source).where(CategoryGroup.workspace_id == workspace_id)
    )
    return {name: gid for gid, name, src in rows.all() if src != source}


def _default_group_id(key: str, group_names: dict[str, uuid.UUID]) -> uuid.UUID | None:
    data = DEFAULT_GROUPS.get(key)
    if data is None:
        return None
    return next((group_names[name] for name in name_variants(data) if name in group_names), None)


def _ids(actions: list[dict[str, Any]]) -> set[str]:
    """The category ids a rule's actions set."""
    return {str(a["value"]) for a in actions if a.get("op") == "set_category" and a.get("value")}


def _resolve_actions(actions: list[dict[str, Any]], category_ref: Any) -> list[dict[str, Any]]:
    return [
        {**action, "value": category_ref(action["value"])} if action["op"] == "set_category" else action
        for action in actions
    ]


# --- Applying ---------------------------------------------------------------------


async def apply_sync(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    source: str,
    manifest: Manifest | None,
    token: str,
    *,
    lang: str = "en",
    create_default_categories: bool = False,
    fingerprint: str | None = None,
) -> SyncResult:
    """Carry out the plan `token` names, or raise `SyncPlanChanged` and change nothing.

    Flushes without committing; the caller commits.
    """
    plan, state = await _plan(
        session, workspace_id, source, manifest, lang, create_default_categories, lock=True, fingerprint=fingerprint
    )
    if plan.token != token:
        await session.rollback()
        raise SyncPlanChanged
    result = SyncResult(plan, {kind: [] for kind in KINDS})
    contributes = manifest.contributes if manifest else None

    if plan.default_categories:
        await ensure_default_categories(session, workspace_id, user_id, set(plan.default_categories), lang)

    # Groups, then categories, then rules: each refers to the ones before it.
    group_ids: dict[str, uuid.UUID] = {key: row.id for key, row in state.rows[GROUPS].items()}
    group_ids |= state.adopted[GROUPS]
    if contributes:
        last = await session.scalar(
            select(func.max(CategoryGroup.position)).where(CategoryGroup.workspace_id == workspace_id)
        )
        position = -1 if last is None else int(last)
        targets = {g.key: g for g in contributes.category_groups}
        for item in plan.of("create", GROUPS):
            spec = targets[item.key]
            position += 1
            row = CategoryGroup(
                user_id=user_id, workspace_id=workspace_id, name=item.name,
                icon=spec.icon, color=spec.color, position=position, is_system=False,
            )
            session.add(row)
            result.created[GROUPS].append(row)
            _stamp(row, source, item.key)
        for item in plan.of("update", GROUPS):
            spec, row = targets[item.key], state.rows[GROUPS][item.key]
            row.name, row.icon, row.color = localized(spec.name, lang), spec.icon, spec.color
            _stamp(row, source, item.key)
        await session.flush()
        group_ids |= {row.source_key: row.id for row in result.created[GROUPS]}

        # Creating default categories may have created their default groups too.
        group_names = await _group_names(session, workspace_id, source)
        categories = {c.key: c for c in contributes.categories}
        for item in plan.of("create", CATEGORIES) + plan.of("update", CATEGORIES):
            spec = categories[item.key]
            group_id = group_ids.get(spec.group) if spec.group in group_ids else (
                _default_group_id(spec.group, group_names) if spec.group else None
            )
            if item in plan.create:
                row = Category(user_id=user_id, workspace_id=workspace_id, is_system=False)
                session.add(row)
                result.created[CATEGORIES].append(row)
            else:
                row = state.rows[CATEGORIES][item.key]
            row.name, row.icon, row.color = localized(spec.name, lang), spec.icon, spec.color
            row.group_id, row.treat_as_transfer = group_id, spec.treat_as_transfer
            _stamp(row, source, item.key)
        await session.flush()

        # Rules resolve their categories against the workspace as it is now.
        category_rows = (
            await session.execute(
                select(Category.id, Category.name, Category.source, Category.source_key).where(
                    Category.workspace_id == workspace_id
                )
            )
        ).all()
        default_ids = resolve_default_category_keys({name: str(cid) for cid, name, _, _ in category_rows})
        own = {key: str(cid) for cid, _, src, key in category_rows if src == source}
        own |= {key: str(cid) for key, cid in state.adopted[CATEGORIES].items()}
        own_keys = {c.key for c in contributes.categories}

        def resolve(key: str) -> str | None:
            return own.get(key) if key in own_keys else default_ids.get(key)

        rules = {r.key: r for r in contributes.rules}
        for item in plan.of("create", RULES) + plan.of("update", RULES):
            spec = rules[item.key]
            template = spec.template()
            actions = _resolve_actions(template["actions"], resolve)
            await validate_rule_definition(session, workspace_id, template["conditions"], actions)
            if item in plan.create:
                row = Rule(user_id=user_id, workspace_id=workspace_id, is_active=True)
                session.add(row)
                result.created[RULES].append(row)
            else:
                row = state.rows[RULES][item.key]
            row.name, row.conditions_op = spec.name, spec.conditions_op
            row.conditions, row.actions, row.priority = template["conditions"], actions, spec.priority
            _stamp(row, source, item.key)

    for kind in (RULES, CATEGORIES, GROUPS):
        for item in plan.of("delete", kind):
            await session.delete(state.rows[kind][item.key])
        await session.flush()
    for item in plan.release:
        row = state.rows[item.kind][item.key]
        row.source = row.source_key = row.source_hash = None

    # The caller commits, so anything it records about the plugin lands in
    # the same transaction as the items.
    await session.flush()
    return result


def _stamp(row: Any, source: str, key: str) -> None:
    """Record the row's origin and its content as the plugin wrote it."""
    row.source, row.source_key = source, key
    row.source_hash = row_hash(row)
