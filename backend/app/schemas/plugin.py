from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InstalledPluginRead(BaseModel):
    """A catalog plugin installed in this workspace, as recorded here: no catalog needed."""

    model_config = ConfigDict(from_attributes=True)

    plugin_id: str
    name: dict[str, str]
    version: str
    commit: str
    installed_at: datetime


class PluginContentsRead(BaseModel):
    category_groups: int
    categories: int
    rules: int


class PluginCatalogRead(BaseModel):
    """A plugin in the catalog and where this workspace stands with it."""

    id: str
    repository: str
    commit: str
    revoked: bool
    revoked_reason: str | None = None
    error: str | None = Field(default=None, description="Why the plugin's manifest couldn't be read.")
    name: dict[str, str] | None = None
    description: dict[str, str] | None = None
    version: str | None = None
    license: str | None = None
    min_securo_version: str | None = None
    contributes: PluginContentsRead | None = None
    compatible: bool = False
    installed_version: str | None = None
    update_available: bool = False


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
