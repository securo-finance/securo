"""The plugin manifest, `securo-plugin.json`, in its v1 form.

Every plugin: identity, a minimum Securo version and what it contributes. A
plugin with code adds `runtime` (its entrypoint and hashed lockfile),
`network` (each host it may reach, and why) and, if it reads or writes the
workspace, `api_scopes` (each scope, and why). Those three are what the
person installing it consents to. A contribution this version can't apply
is rejected rather than half installed.

References between items are plain keys: a key names an item of the same
plugin first, and a built-in default otherwise. A plugin can't point at
another plugin's items.
"""
from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.access_token_scopes import ALL_SCOPES
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
#: Contributions applied as data: nothing of the plugin runs.
DATA_KINDS = frozenset({"category_groups", "categories", "rules"})
#: Contributions that run the plugin's code, in the plugin runner.
CODE_KINDS = frozenset({"integrations"})
#: Contributions to the interface, drawn by Securo's own components: nothing runs.
UI_KINDS = frozenset({"themes"})
ALL_KINDS = DATA_KINDS | CODE_KINDS | UI_KINDS
#: Design tokens a theme may color; the same list as the frontend's THEME_TOKENS.
THEME_TOKENS = frozenset(
    {"background", "foreground", "card", "primary", "primary-foreground", "muted", "muted-foreground", "border"}
    | {f"chart-{n}" for n in range(1, 13)}
    | {"chart-muted", "chart-income", "chart-expense"}
)
#: Text pairs a theme must keep readable (WCAG AA, 4.5:1), when it sets both colors.
READABLE_PAIRS = (
    ("foreground", "background"),
    ("foreground", "card"),
    ("muted-foreground", "background"),
    ("muted-foreground", "card"),
    ("primary-foreground", "primary"),
)
MIN_CONTRAST = 4.5
#: Securo's own colors (frontend SECURO_COLORS and index.css), which a
#: theme's unset tokens keep: contrast is checked against these too.
DEFAULT_THEME_COLORS = {
    "light": {"background": "#F8F9FB", "foreground": "#0F172A", "card": "#FFFFFF", "primary": "#6366F1",
              "primary-foreground": "#FFFFFF", "muted-foreground": "#64748B"},
    "dark": {"background": "#0C0D12", "foreground": "#F0F0F5", "card": "#16171F", "primary": "#818CF8",
             "primary-foreground": "#FFFFFF", "muted-foreground": "#8A8F9E"},
}
#: A plain host name, optionally with a port; never a wildcard or an address.
HOST_PATTERN = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(:[0-9]{1,5})?$"
ENTRYPOINT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
PATH_PATTERN = r"^[A-Za-z0-9._-][A-Za-z0-9._/-]{0,199}$"

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


class FormField(_Model):
    """One field of an integration's connection form."""

    key: str = Field(pattern=KEY_PATTERN)
    label: Text
    secret: bool = False
    """Kept in the encrypted store and never shown again."""
    required: bool = True


class Integration(_Model):
    """A connection to an outside service, run by the plugin's code.

    `credentials`: the person fills in `fields` (an API key, say).
    `oauth`: the person signs in at the service; the plugin builds the sign-in
    link and trades the code for a token. `none`: nothing to fill in.
    With `sync_minutes`, Securo calls the plugin's `sync` on that schedule.
    """

    key: str = Field(pattern=KEY_PATTERN)
    name: Text
    description: Text | None = None
    auth: Literal["credentials", "oauth", "none"] = "credentials"
    fields: list[FormField] = Field(default=[], max_length=20)
    sync_minutes: int | None = Field(default=None, ge=15, le=7 * 24 * 60)

    @model_validator(mode="after")
    def _unique_fields(self) -> Integration:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"integration {self.key}: field keys must be unique")
        return self


class Runtime(_Model):
    entrypoint: str = Field(pattern=ENTRYPOINT_PATTERN, max_length=200)
    lockfile: str | None = Field(default=None, pattern=PATH_PATTERN)
    """A requirements file with a hash for every package (`uv export` writes one)."""

    @model_validator(mode="after")
    def _inside(self) -> Runtime:
        if self.lockfile is not None and ".." in self.lockfile.split("/"):
            raise ValueError("the lockfile must be a path inside the plugin")
        return self


class NetworkHost(_Model):
    host: str = Field(pattern=HOST_PATTERN, max_length=260)
    purpose: Text


class ApiScope(_Model):
    scope: str
    purpose: Text

    @model_validator(mode="after")
    def _known(self) -> ApiScope:
        if self.scope not in ALL_SCOPES:
            raise ValueError(f"unknown scope: {self.scope}")
        return self


def _luminance(hex_color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    n = int(hex_color[1:], 16)
    return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)


def contrast_ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _theme_colors(colors: dict[str, str]) -> dict[str, str]:
    unknown = set(colors) - THEME_TOKENS
    if unknown:
        raise ValueError(f"unknown theme tokens: {', '.join(sorted(unknown))}")
    for token, value in colors.items():
        # Colors only: anything else (`url(...)`, `var(...)`) could make the
        # browser fetch from elsewhere or reach past the theme's tokens.
        if not isinstance(value, str) or not re.fullmatch(COLOR_PATTERN, value):
            raise ValueError(f"{token} must be a #RRGGBB color")
    return {token: value.upper() for token, value in colors.items()}


ThemeColors = Annotated[dict[str, str], AfterValidator(_theme_colors)]


class Theme(_Model):
    """A look for Securo: colors for its tokens in light and dark, drawn by Securo itself.

    No CSS, no images and no fonts to download: `font` picks one of the
    device's own families.
    """

    key: str = Field(pattern=KEY_PATTERN)
    name: Name
    light: ThemeColors = {}
    dark: ThemeColors = {}
    radius: float | None = Field(default=None, ge=0, le=1)
    """Corner radius in rem; Securo's own is 0.625."""
    font: Literal["sans", "mono", "serif"] | None = None

    @model_validator(mode="after")
    def _readable(self) -> Theme:
        if not (self.light or self.dark):
            raise ValueError(f"theme {self.key}: colors nothing")
        for mode, own in (("light", self.light), ("dark", self.dark)):
            if not own:
                continue  # that mode keeps Securo's look
            colors = {**DEFAULT_THEME_COLORS[mode], **own}
            for text, background in READABLE_PAIRS:
                # A pair the theme leaves alone is Securo's own, already readable.
                if text in own or background in own:
                    ratio = contrast_ratio(colors[text], colors[background])
                    if ratio < MIN_CONTRAST:
                        raise ValueError(
                            f"theme {self.key}: {text} on {background} ({mode}) is hard to read "
                            f"(contrast {ratio:.1f}, needs {MIN_CONTRAST})"
                        )
        return self


class Contributes(_Model):
    category_groups: list[CategoryGroup] = []
    categories: list[Category] = []
    rules: list[Rule] = []
    integrations: list[Integration] = []
    themes: list[Theme] = Field(default=[], max_length=50)

    @model_validator(mode="after")
    def _well_formed(self) -> Contributes:
        if not (self.category_groups or self.categories or self.rules or self.integrations or self.themes):
            raise ValueError("contributes nothing")
        for kind in ("category_groups", "categories", "rules", "integrations", "themes"):
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
    runtime: Runtime | None = None
    network: list[NetworkHost] = Field(default=[], max_length=100)
    api_scopes: list[ApiScope] = Field(default=[], max_length=len(ALL_SCOPES))

    @property
    def has_code(self) -> bool:
        return self.runtime is not None

    @model_validator(mode="after")
    def _code_is_declared(self) -> Manifest:
        if self.contributes.integrations and self.runtime is None:
            raise ValueError("integrations need a runtime")
        if self.runtime is None and (self.network or self.api_scopes):
            raise ValueError("network and api_scopes are only for plugins with a runtime")
        if self.runtime is not None and not self.contributes.integrations:
            raise ValueError("a runtime needs something to run: add an integration")
        hosts = [h.host for h in self.network]
        scopes = [s.scope for s in self.api_scopes]
        if len(hosts) != len(set(hosts)) or len(scopes) != len(set(scopes)):
            raise ValueError("network hosts and api_scopes must be unique")
        return self

    @model_validator(mode="before")
    @classmethod
    def _known_kinds(cls, data: Any) -> Any:
        if isinstance(data, dict):
            contributes = data.get("contributes")
            if isinstance(contributes, dict):
                # An empty list of a kind this version doesn't know contributes
                # nothing, as the index counts it: drop it rather than refuse.
                kept = {k: v for k, v in contributes.items() if str(k) in ALL_KINDS or v not in ([], {}, None)}
                unsupported = {str(kind) for kind in kept} - ALL_KINDS
                if unsupported:
                    raise ValueError(
                        f"this Securo version can't apply: {', '.join(sorted(unsupported))}"
                    )
                data = {**data, "contributes": kept}
        return data


def parse_manifest(data: Any) -> Manifest:
    """Validate a manifest; raises `pydantic.ValidationError` when it isn't one."""
    return Manifest.model_validate(data)
