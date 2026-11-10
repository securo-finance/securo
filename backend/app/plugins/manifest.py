"""The plugin manifest, `securo-plugin.json`, in its v1 form.

Only what a data plugin needs: identity, a minimum Securo version and what it
contributes. A plugin that declares code (`runtime`, `network`, `api_scopes`)
or a contribution this version can't apply is rejected rather than half
installed.

References between items are plain keys: a key names an item of the same
plugin first, and a built-in default otherwise. A plugin can't point at
another plugin's items.
"""
from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.services.category_defaults import DEFAULT_CATEGORIES, DEFAULT_GROUPS
from app.services.rule_engine import compile_rule_regex

KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
LOCALE_PATTERN = r"^[a-z]{2,3}(-[A-Z]{2})?$"
VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$"
REPOSITORY_PATTERN = r"^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
#: The longest name a category or group column holds.
NAME_MAX = 100
#: Contributions this version can apply.
DATA_KINDS = frozenset({"category_groups", "categories", "rules"})

#: Condition fields a plugin rule may use. Account and payee ids are a
#: workspace's own data, so a plugin can't know them.
ConditionField = Literal["description", "payee", "notes", "amount", "type", "date", "status"]
ConditionOp = Literal[
    "contains", "not_contains", "equals", "not_equals", "starts_with",
    "ends_with", "regex", "gt", "gte", "lt", "lte",
]
#: `set_payee` takes a payee id, which a plugin can't know either.
ActionOp = Literal["set_category", "set_description", "append_notes", "ignore"]


def version_tuple(version: str) -> tuple[int, ...]:
    """`0.19.0` as (0, 19, 0); suffixes such as `.dev1` or `rc1` are ignored."""
    parts = []
    for part in version.split(".")[:3]:
        digits = re.match(r"\d+", part)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _translated(text: Any) -> Any:
    """Plain text, or text per locale with English as the fallback."""
    if isinstance(text, str):
        return {"en": text}
    if isinstance(text, dict):
        if "en" not in text:
            raise ValueError("translated text needs an `en` entry")
        bad = [locale for locale in text if not re.match(LOCALE_PATTERN, str(locale))]
        if bad:
            raise ValueError(f"not a locale: {', '.join(map(str, bad))}")
    return text


Text = Annotated[dict[str, str], BeforeValidator(_translated)]


def _short_name(text: dict[str, str]) -> dict[str, str]:
    long = [locale for locale, name in text.items() if len(name.strip()) > NAME_MAX]
    if long:
        raise ValueError(f"names are limited to {NAME_MAX} characters ({', '.join(long)})")
    if not all(name.strip() for name in text.values()):
        raise ValueError("names can't be blank")
    return text


#: A category or group name: translated, and short enough to store.
Name = Annotated[Text, AfterValidator(_short_name)]


def localized(text: dict[str, str], lang: str) -> str:
    """`text` in `lang`, its base language, or English."""
    return text.get(lang) or text.get(lang.split("-")[0]) or text["en"]


class Condition(_Model):
    field: ConditionField
    op: ConditionOp
    value: str | int | float

    @model_validator(mode="after")
    def _valid_value(self) -> Condition:
        if self.op == "regex":
            # The policy rules are held to when created, so a plugin can't ship one
            # that matches everything (".*") and only fails once applied.
            compile_rule_regex(str(self.value))
        if self.field == "status" and (
            self.op not in ("equals", "not_equals") or str(self.value).lower() not in ("pending", "posted")
        ):
            raise ValueError("status compares with equals/not_equals to pending or posted")
        return self


class ConditionGroup(_Model):
    """Leaves joined by their own operator, keyed as the rule engine reads it."""

    op: Literal["and", "or"] = "or"
    conditions: list[Condition] = Field(min_length=1)


class Action(_Model):
    op: ActionOp
    value: str | bool | None = None

    @model_validator(mode="after")
    def _valid_value(self) -> Action:
        if self.op in ("set_category", "set_description", "append_notes"):
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError(f"{self.op} needs a text value")
        if self.op == "set_description" and isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("set_description is limited to 500 characters")
        return self


class Rule(_Model):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    conditions_op: Literal["and", "or"]
    conditions: list[Condition | ConditionGroup] = Field(min_length=1)
    actions: list[Action] = Field(min_length=1)
    priority: int = Field(default=0, ge=0, le=1000)

    def template(self) -> dict[str, Any]:
        """A fresh rule template, in the shape rules are stored in."""
        return copy.deepcopy(
            {
                "key": self.key,
                "name": self.name,
                "conditions_op": self.conditions_op,
                "conditions": [c.model_dump(exclude_none=True) for c in self.conditions],
                "actions": [a.model_dump(exclude_none=True) for a in self.actions],
                "priority": self.priority,
            }
        )

    def category_keys(self) -> set[str]:
        return {str(a.value) for a in self.actions if a.op == "set_category"}


class CategoryGroup(_Model):
    key: str = Field(pattern=KEY_PATTERN)
    name: Name
    icon: str = Field(default="folder", max_length=50)
    color: str = Field(default="#6B7280", pattern=COLOR_PATTERN)


class Category(_Model):
    key: str = Field(pattern=KEY_PATTERN)
    name: Name
    group: str | None = Field(default=None, pattern=KEY_PATTERN)
    icon: str = Field(default="circle-help", max_length=50)
    color: str = Field(default="#6B7280", pattern=COLOR_PATTERN)
    treat_as_transfer: bool = False


class Contributes(_Model):
    category_groups: list[CategoryGroup] = []
    categories: list[Category] = []
    rules: list[Rule] = []

    @model_validator(mode="after")
    def _well_formed(self) -> Contributes:
        if not (self.category_groups or self.categories or self.rules):
            raise ValueError("contributes nothing")
        for kind in ("category_groups", "categories", "rules"):
            keys = [item.key for item in getattr(self, kind)]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{kind}: keys must be unique")
        # Rules are told apart by name in a workspace, as when created by hand.
        names = [rule.name.strip().casefold() for rule in self.rules]
        if len(names) != len(set(names)):
            raise ValueError("rules: names must be unique")
        groups = {g.key for g in self.category_groups} | set(DEFAULT_GROUPS)
        for category in self.categories:
            if category.group is not None and category.group not in groups:
                raise ValueError(f"category {category.key}: unknown group {category.group}")
        categories = {c.key for c in self.categories} | set(DEFAULT_CATEGORIES)
        for rule in self.rules:
            missing = rule.category_keys() - categories
            if missing:
                raise ValueError(f"rule {rule.key}: unknown category {', '.join(sorted(missing))}")
        return self


class Manifest(_Model):
    id: str = Field(pattern=ID_PATTERN)
    name: Text
    description: Text
    version: str = Field(pattern=VERSION_PATTERN, max_length=32)
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    license: str = Field(min_length=2, max_length=64)
    min_securo_version: str = Field(pattern=VERSION_PATTERN)
    contributes: Contributes

    @model_validator(mode="before")
    @classmethod
    def _only_data(cls, data: Any) -> Any:
        if isinstance(data, dict):
            code = [field for field in ("runtime", "network", "api_scopes") if field in data]
            if code:
                raise ValueError(f"plugins with code aren't supported yet ({', '.join(code)})")
            contributes = data.get("contributes")
            if isinstance(contributes, dict):
                # An empty list of a kind this version doesn't know contributes
                # nothing, as the index counts it: drop it rather than refuse.
                kept = {k: v for k, v in contributes.items() if str(k) in DATA_KINDS or v not in ([], {}, None)}
                unsupported = {str(kind) for kind in kept} - DATA_KINDS
                if unsupported:
                    raise ValueError(
                        f"this Securo version can't apply: {', '.join(sorted(unsupported))}"
                    )
                data = {**data, "contributes": kept}
        return data


def parse_manifest(data: Any) -> Manifest:
    """Validate a manifest; raises `pydantic.ValidationError` when it isn't one."""
    return Manifest.model_validate(data)
