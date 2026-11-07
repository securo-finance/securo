"""Rule packs shipped with Securo, read from `app/data/rule_packs/*.json`.

Each file is a plugin manifest (v1) that contributes only rules, so the
built-in packs go through the same contract a third-party pack will. They
are part of the core and work whether or not plugins are enabled.

A rule installed from a pack records its origin (`Rule.source`,
`Rule.source_key`) and a hash of its content at install time
(`Rule.source_hash`). Comparing that hash with the rule as it is now tells an
untouched pack rule from one the user edited.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.services.category_defaults import DEFAULT_CATEGORIES, name_variants

PACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "rule_packs"

#: The built-in packs, in the order they are listed. Each one is
#: `PACKS_DIR/<name>.json` with the manifest id `securo/rules-<name>`.
PACK_FILES = ("br", "us", "eu", "gb")

_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_LOCALE_PATTERN = r"^[a-z]{2,3}(-[A-Z]{2})?$"
_VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


# --- Manifest (the subset a rule pack uses) ----------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _translated(text: str | dict[str, str]) -> dict[str, str]:
    """Plain text, or text per locale with English as the fallback."""
    if isinstance(text, str):
        return {"en": text}
    if "en" not in text:
        raise ValueError("translated text needs an `en` entry")
    bad = [locale for locale in text if not re.match(_LOCALE_PATTERN, locale)]
    if bad:
        raise ValueError(f"not a locale: {', '.join(bad)}")
    return text


Text = Annotated[dict[str, str], BeforeValidator(_translated)]


class _ManifestRule(_Model):
    key: str = Field(pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    conditions_op: Literal["and", "or"]
    conditions: list[dict[str, Any]] = Field(min_length=1)
    actions: list[dict[str, Any]] = Field(min_length=1)
    priority: int = Field(default=0, ge=0, le=1000)


class _Contributes(_Model):
    """A rule pack contributes rules and nothing else."""

    rules: list[_ManifestRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _rule_keys_are_unique(self) -> _Contributes:
        keys = [r.key for r in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("rule keys must be unique")
        return self


class _Manifest(_Model):
    """A data plugin needs no runtime, network or scopes, so it can't declare them."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$")
    name: Text
    description: Text
    version: str = Field(pattern=_VERSION_PATTERN)
    repository: str = Field(pattern=r"^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    license: str = Field(min_length=2)
    min_securo_version: str = Field(pattern=_VERSION_PATTERN)
    contributes: _Contributes


# --- Loaded packs --------------------------------------------------------------


@dataclass(frozen=True)
class PackRule:
    key: str
    name: str
    conditions_op: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    priority: int

    def template(self) -> dict[str, Any]:
        """A fresh rule template, the shape `rule_service` builds rules from."""
        return {
            "key": self.key,
            "name": self.name,
            "conditions_op": self.conditions_op,
            "conditions": copy.deepcopy(self.conditions),
            "actions": copy.deepcopy(self.actions),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class RulePack:
    code: str
    """What the API calls the pack (`BR`)."""
    source: str
    """The manifest id, recorded on every rule the pack installs."""
    version: str
    names: dict[str, str]
    """The pack's name per locale; always has `en`."""
    rules: tuple[PackRule, ...]

    @property
    def name(self) -> str:
        return self.names["en"]

    @property
    def flag(self) -> str:
        """The regional-indicator flag for the code (EU has one too)."""
        return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in self.code)

    def category_keys(self) -> set[str]:
        """Default-category keys the pack's rules assign."""
        return {
            action["value"]
            for rule in self.rules
            for action in rule.actions
            if action["op"] == "set_category"
        }


def load_pack(path: Path) -> RulePack:
    manifest = _Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return RulePack(
        code=path.stem.upper(),
        source=manifest.id,
        version=manifest.version,
        names=manifest.name,
        rules=tuple(
            PackRule(
                key=r.key,
                name=r.name,
                conditions_op=r.conditions_op,
                conditions=r.conditions,
                actions=r.actions,
                priority=r.priority,
            )
            for r in manifest.contributes.rules
        ),
    )


@cache
def built_in_packs() -> dict[str, RulePack]:
    packs = (load_pack(PACKS_DIR / f"{name}.json") for name in PACK_FILES)
    return {pack.code: pack for pack in packs}


def get_pack(code: str) -> RulePack | None:
    return built_in_packs().get(code)


# --- Telling untouched rules from edited ones -------------------------------------


def rule_content_hash(
    *,
    name: str,
    conditions_op: str,
    conditions: Any,
    actions: Any,
    priority: int,
) -> str:
    """Fingerprint of everything the rule editor can change but `is_active`:
    switching a pack rule off doesn't make it the user's own."""
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
    matches = [
        (pack, rule)
        for pack in built_in_packs().values()
        for rule in pack.rules
        if rule.name == name
        and rule.conditions_op == conditions_op
        and rule.conditions == conditions
        and rule.priority == priority
        and _same_actions(actions, rule.actions, category_names)
    ]
    return matches[0] if len(matches) == 1 else None


def _same_actions(
    actions: Any, expected: list[dict[str, Any]], category_names: Mapping[str, str]
) -> bool:
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
