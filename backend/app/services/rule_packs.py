"""Rule packs shipped with Securo, read from `app/data/rule_packs/*.json`.

Each file is a plugin manifest (v1) that contributes only rules, so the
built-in packs go through the same contract a third-party pack will. They
are part of the core: they work whether or not plugins are enabled.

A rule installed from a pack remembers where it came from (`source`,
`source_key`) and a hash of what it looked like when installed
(`source_hash`). Comparing that hash with the rule as it is now tells an
untouched pack rule from one the user edited, which is what lets a pack be
removed without taking anything the user changed.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "rule_packs"

_KEY = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Author(_Strict):
    name: str
    url: str | None = None
    github: str | None = None


class _AppliesTo(_Strict):
    currencies: list[str] = []
    countries: list[str] = []
    locales: list[str] = []
    workspace_kinds: list[str] = []


class _Privacy(_Strict):
    data_leaves_instance: Literal[False]


class _PackRule(_Strict):
    key: str = Field(pattern=_KEY)
    name: dict[str, str]
    conditions_op: Literal["and", "or"]
    conditions: list[dict[str, Any]] = Field(min_length=1)
    actions: list[dict[str, Any]] = Field(min_length=1)
    priority: int = Field(default=0, ge=0, le=1000)


class _Contributes(_Strict):
    rules: list[_PackRule] = Field(min_length=1)


class _Manifest(_Strict):
    schema_: str | None = Field(default=None, alias="$schema")
    manifest_version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$")
    version: str
    default_locale: str
    name: dict[str, str]
    description: dict[str, str]
    author: _Author
    repository: str
    license: str
    securo: str
    applies_to: _AppliesTo | None = None
    privacy: _Privacy
    contributes: _Contributes

    @model_validator(mode="after")
    def _texts_have_the_default_locale(self) -> _Manifest:
        if not _LOCALE.match(self.default_locale):
            raise ValueError(f"bad default_locale {self.default_locale!r}")
        texts = [("name", self.name), ("description", self.description)]
        texts += [(f"rule {r.key}", r.name) for r in self.contributes.rules]
        for label, text in texts:
            if self.default_locale not in text:
                raise ValueError(f"{label} has no {self.default_locale} text")
        keys = [r.key for r in self.contributes.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("rule keys must be unique")
        return self


@dataclass(frozen=True)
class RulePack:
    code: str
    """Short code used by the API (`BR`), the end of the id upper-cased."""
    source: str
    """The manifest id, stored on every rule the pack installs."""
    version: str
    default_locale: str
    names: dict[str, str]
    rules: tuple[dict[str, Any], ...]
    """Rule templates: key, name (in the default locale) and definition."""

    @property
    def name(self) -> str:
        return self.names.get("en") or self.names[self.default_locale]

    @property
    def flag(self) -> str:
        """Regional-indicator flag for the code (EU has one too)."""
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in self.code)


def _code(source: str) -> str:
    return source.split("/", 1)[1].removeprefix("rules-").upper()


def load_pack(path: Path) -> RulePack:
    manifest = _Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    loc = manifest.default_locale
    rules = tuple(
        {
            "key": r.key,
            "name": r.name[loc],
            "conditions_op": r.conditions_op,
            "conditions": r.conditions,
            "actions": r.actions,
            "priority": r.priority,
        }
        for r in manifest.contributes.rules
    )
    return RulePack(
        code=_code(manifest.id),
        source=manifest.id,
        version=manifest.version,
        default_locale=loc,
        names=manifest.name,
        rules=rules,
    )


@lru_cache(maxsize=1)
def built_in_packs() -> dict[str, RulePack]:
    packs = [load_pack(p) for p in sorted(PACKS_DIR.glob("*.json"))]
    # Keep the order the packs have always been listed in.
    order = {"BR": 0, "US": 1, "EU": 2, "GB": 3}
    packs.sort(key=lambda p: (order.get(p.code, len(order)), p.code))
    return {p.code: p for p in packs}


def rule_content_hash(
    name: str,
    conditions_op: str,
    conditions: Any,
    actions: Any,
    priority: int,
) -> str:
    """Fingerprint of a rule's user-editable content.

    Covers everything the rule editor can change except whether the rule is
    active: switching a pack rule off doesn't make it the user's own.
    """
    payload = json.dumps(
        {
            "name": name,
            "conditions_op": conditions_op,
            "conditions": conditions,
            "actions": actions,
            "priority": priority,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def match_installed_rule(
    name: str,
    conditions_op: str,
    conditions: Any,
    actions: Any,
    priority: int,
    category_name: dict[str, str],
) -> tuple[str, str] | None:
    """The pack rule an existing, untouched rule was installed from.

    For rules installed before packs recorded their origin. A rule only
    matches when it is exactly what installing the pack produced: same name,
    conditions and priority, and actions that differ only in the category
    being this workspace's copy of the pack's default category
    (`category_name` maps the workspace's category ids to their names). An
    edited rule never matches, so it stays the user's.
    """
    from app.services.category_defaults import DEFAULT_CATEGORIES, name_variants

    def same_actions(template: list[dict[str, Any]]) -> bool:
        if not isinstance(actions, list) or len(actions) != len(template):
            return False
        for have, want in zip(actions, template):
            if not isinstance(have, dict) or have.get("op") != want["op"]:
                return False
            if want["op"] == "set_category":
                data = DEFAULT_CATEGORIES.get(want["value"])
                cat = category_name.get(str(have.get("value")))
                if data is None or cat is None or cat not in name_variants(data):
                    return False
            elif have != want:
                return False
        return True

    found = None
    for pack in built_in_packs().values():
        for rule in pack.rules:
            if (
                rule["name"] == name
                and rule["conditions_op"] == conditions_op
                and rule["conditions"] == conditions
                and rule["priority"] == priority
                and same_actions(rule["actions"])
            ):
                if found is not None:
                    return None  # two packs fit equally well: don't guess
                found = (pack.source, rule["key"])
    return found
