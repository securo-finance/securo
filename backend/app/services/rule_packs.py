"""Rule packs shipped with Securo, read from `app/data/rule_packs/*.json`.

Each file is a plugin manifest (v1) that contributes only rules, and each is
installed, updated and removed by the same sync a catalog plugin goes
through (`app.plugins.sync`). They are part of the core: they work whether
or not plugins are enabled, and have no catalog entry.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule
from app.plugins import sync
from app.plugins.manifest import Manifest, parse_manifest
from app.plugins.manifest import Rule as PackRule
from app.services.category_defaults import DEFAULT_CATEGORIES, name_variants
from app.services.rule_service import existing_rule_names

PACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "rule_packs"

#: The built-in packs, in the order they are listed. Each one is
#: `PACKS_DIR/<name>.json` with the manifest id `securo/rules-<name>`.
PACK_FILES = ("br", "us", "eu", "gb")


@dataclass(frozen=True)
class RulePack:
    code: str
    """What the API calls the pack (`BR`): its file name, upper-cased."""
    manifest: Manifest

    @property
    def source(self) -> str:
        """The manifest id, recorded on every rule the pack installs."""
        return self.manifest.id

    @property
    def names(self) -> dict[str, str]:
        """The pack's name per locale; always has `en`."""
        return self.manifest.name

    @property
    def name(self) -> str:
        return self.names["en"]

    @property
    def rules(self) -> list[PackRule]:
        return self.manifest.contributes.rules

    @property
    def flag(self) -> str:
        """The regional-indicator flag for the code (EU has one too)."""
        return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in self.code)


def load_pack(path: Path) -> RulePack:
    manifest = parse_manifest(json.loads(path.read_text(encoding="utf-8")))
    if manifest.contributes.categories or manifest.contributes.category_groups:
        raise ValueError(f"{path.name}: a built-in rule pack contributes rules only")
    return RulePack(code=path.stem.upper(), manifest=manifest)


@cache
def built_in_packs() -> dict[str, RulePack]:
    packs = (load_pack(PACKS_DIR / f"{name}.json") for name in PACK_FILES)
    return {pack.code: pack for pack in packs}


def get_pack(code: str) -> RulePack | None:
    return built_in_packs().get(code)


# --- Installing, listing and removing ----------------------------------------------


@dataclass(frozen=True)
class RulePackInstallResult:
    rules: list[Rule]
    unresolved: int
    categories_created: int = 0


async def install_rule_pack(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    pack_code: str,
    lang: str = "pt-BR",
    create_missing_categories: bool = False,
) -> RulePackInstallResult:
    """Install (or bring up to date) a built-in pack in a workspace, without a preview.

    Rules the workspace already has, by key or by name, are left alone. With
    `create_missing_categories`, default categories the pack needs and the
    workspace lacks are created first, named in `lang`; otherwise rules that
    need them are skipped and counted as unresolved.
    """
    pack = get_pack(pack_code)
    if pack is None:
        return RulePackInstallResult([], 0)
    plan = await sync.plan_sync(
        session, workspace_id, pack.source, pack.manifest,
        lang=lang, create_default_categories=create_missing_categories,
    )
    result = await sync.apply_sync(
        session, workspace_id, user_id, pack.source, pack.manifest, plan.token,
        lang=lang, create_default_categories=create_missing_categories,
    )
    await session.commit()
    return RulePackInstallResult(
        rules=result.created[sync.RULES],
        unresolved=len(plan.unresolved),
        categories_created=len(plan.default_categories),
    )


@dataclass(frozen=True)
class RulePackStatus:
    pack: RulePack
    installed: bool
    """Every rule of the pack is in the workspace, by key or by name."""
    rules_from_pack: int
    """Rules in the workspace that still come from the pack."""


async def list_rule_packs(session: AsyncSession, workspace_id: uuid.UUID) -> list[RulePackStatus]:
    """The built-in packs and where the workspace stands with each.

    Rules installed before packs recorded their origin carry no key, so a
    pack rule also counts as present when a rule has its name, as it always
    did.
    """
    names = await existing_rule_names(session, workspace_id)
    rows = (
        await session.execute(
            select(Rule.source, Rule.source_key).where(Rule.workspace_id == workspace_id, Rule.source.isnot(None))
        )
    ).all()
    keys_by_source: dict[str, set[str]] = defaultdict(set)
    for source, key in rows:
        keys_by_source[source].add(key)
    count_by_source = Counter(source for source, _ in rows)
    return [
        RulePackStatus(
            pack=pack,
            installed=all(r.key in keys_by_source[pack.source] or r.name in names for r in pack.rules),
            rules_from_pack=count_by_source[pack.source],
        )
        for pack in built_in_packs().values()
    ]


@dataclass(frozen=True)
class RuleRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class RulePackRemoval:
    remove: list[RuleRef]
    """Pack rules still exactly as installed: removing the pack deletes them."""
    keep: list[RuleRef]
    """Pack rules the user edited: they stay, as the user's own."""
    token: str
    """Identifies this plan; removal is refused under another."""


def _removal(plan: sync.SyncPlan) -> RulePackRemoval:
    def refs(bucket: str) -> list[RuleRef]:
        return [RuleRef(id=i.id, name=i.name) for i in plan.of(bucket, sync.RULES) if i.id is not None]

    return RulePackRemoval(remove=refs("delete"), keep=refs("release"), token=plan.token)


async def plan_rule_pack_removal(
    session: AsyncSession, workspace_id: uuid.UUID, pack_code: str
) -> RulePackRemoval | None:
    """What removing a pack would delete and what it would keep.

    Only rules that record coming from the pack are considered; a rule with
    no recorded origin is never touched.
    """
    pack = get_pack(pack_code)
    if pack is None:
        return None
    return _removal(await sync.plan_sync(session, workspace_id, pack.source, None))


async def remove_rule_pack(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, pack_code: str, token: str
) -> RulePackRemoval | None:
    """Remove a pack: delete its untouched rules and hand the edited ones over.

    `token` is the one from the plan the user confirmed; if the plan differs
    now, `sync.SyncPlanChanged` is raised and nothing is removed.
    Transactions keep the categories the rules already gave them.
    """
    pack = get_pack(pack_code)
    if pack is None:
        return None
    result = await sync.apply_sync(session, workspace_id, user_id, pack.source, None, token)
    await session.commit()
    return _removal(result.plan)


# --- Rules installed before packs recorded their origin -----------------------------


def find_pack_rule(
    *,
    name: str,
    conditions_op: str,
    conditions: Any,
    actions: Any,
    priority: int,
    category_names: Mapping[str, str],
) -> tuple[RulePack, PackRule] | None:
    """The pack rule an existing rule is an untouched copy of, if any.

    For rules installed before packs recorded their origin. A rule matches
    only when it is exactly what installing the pack produced: same name,
    conditions and priority, and the same actions with the category being
    this workspace's copy of the pack's default one (`category_names` maps
    category ids to names). An edited rule never matches, and neither does a
    rule two packs could equally have produced.
    """
    matches = []
    for pack in built_in_packs().values():
        for rule in pack.rules:
            template = rule.template()
            if (
                rule.name == name
                and rule.conditions_op == conditions_op
                and template["conditions"] == conditions
                and rule.priority == priority
                and _same_actions(actions, template["actions"], category_names)
            ):
                matches.append((pack, rule))
    return matches[0] if len(matches) == 1 else None


def _same_actions(actions: Any, expected: list[dict[str, Any]], category_names: Mapping[str, str]) -> bool:
    if not isinstance(actions, list) or len(actions) != len(expected):
        return False
    for action, want in zip(actions, expected):
        if not isinstance(action, dict) or action.get("op") != want["op"]:
            return False
        if want["op"] != "set_category":
            if action != want:
                return False
            continue
        default = DEFAULT_CATEGORIES.get(want["value"])
        category = category_names.get(str(action.get("value")))
        if default is None or category not in name_variants(default):
            return False
    return True
