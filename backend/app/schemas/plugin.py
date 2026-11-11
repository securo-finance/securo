from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InstalledPluginRead(BaseModel):
    """A catalog plugin installed in this workspace, as recorded here: no catalog needed."""

    model_config = ConfigDict(from_attributes=True)

    plugin_id: str
    name: dict[str, str]
    version: str
    commit: str
    installed_at: datetime


class PluginCatalogRead(BaseModel):
    """A plugin in the catalog and where this workspace stands with it."""

    id: str
    owner: str
    repository: str
    release: str
    commit: str
    name: dict[str, str]
    description: dict[str, str]
    version: str
    license: str
    min_securo_version: str
    contributes: dict[str, int] = Field(description="How many items of each kind the plugin brings.")
    locales: list[str]
    topics: list[str]
    stars: int
    stars_7d: int = Field(description="Stars gained in the last 7 days.")
    added_at: datetime | None
    updated_at: datetime | None
    revoked: bool
    revoked_reason: str | None
    compatible: bool = Field(description="This Securo is at least min_securo_version.")
    supported: bool = Field(description="This Securo can apply everything the plugin contributes.")
    installed_version: str | None
    update_available: bool


class PluginRefRead(BaseModel):
    """An item a plugin points at: one of its own, or a built-in default."""

    key: str
    name: dict[str, str]
    default: bool


class PluginGroupRead(BaseModel):
    key: str
    name: dict[str, str]
    icon: str
    color: str


class PluginCategoryRead(PluginGroupRead):
    group: PluginRefRead | None
    treat_as_transfer: bool


class PluginRuleActionRead(BaseModel):
    op: str
    value: str | bool | None = None
    category: PluginRefRead | None = Field(description="For set_category: the category it sets.")


class PluginRuleRead(BaseModel):
    key: str
    name: str
    priority: int
    conditions_op: Literal["and", "or"]
    conditions: list[dict[str, Any]] = Field(description="Leaves (field, op, value) and groups (op, conditions).")
    actions: list[PluginRuleActionRead]


class PluginContentsRead(BaseModel):
    category_groups: list[PluginGroupRead]
    categories: list[PluginCategoryRead]
    rules: list[PluginRuleRead]


class PluginDetailRead(BaseModel):
    """One plugin opened from the catalog."""

    plugin: PluginCatalogRead
    readme: str | None = Field(description="Markdown, from the catalog's verified copy.")
    contents: PluginContentsRead | None = Field(description="Null when this Securo can't read what it contains.")


class PluginItemRead(BaseModel):
    kind: Literal["category_groups", "categories", "rules"]
    key: str
    name: str


class PluginPlanRead(BaseModel):
    """What installing, updating or removing a plugin does in this workspace."""

    action: Literal["install", "update", "remove"]
    plugin_id: str
    version: str | None
    commit: str | None = Field(description="The commit this plan installs; null when removing.")
    token: str = Field(description="Pass it back to apply; refused if the plan changed since.")
    create: list[PluginItemRead]
    update: list[PluginItemRead]
    delete: list[PluginItemRead]
    keep: list[PluginItemRead] = Field(description="Edited by the user; left as they are.")
    release: list[PluginItemRead] = Field(
        description="No longer the plugin's (edited, or still in use); handed over to the user."
    )
    existing: list[PluginItemRead] = Field(description="Already in the workspace by name; left alone.")
    unresolved: list[PluginItemRead]
    default_categories: list[str] = Field(description="Default categories created first for the plugin's rules.")


class PluginApplyRequest(BaseModel):
    action: Literal["install", "update", "remove"]
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    token: str = Field(min_length=64, max_length=64)
