import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PluginFieldRead(BaseModel):
    key: str
    label: dict[str, str]
    secret: bool
    required: bool


class PluginIntegrationRead(BaseModel):
    key: str
    name: dict[str, str]
    description: dict[str, str] | None = None
    auth: Literal["credentials", "oauth", "none"]
    fields: list[PluginFieldRead] = []
    sync_minutes: int | None = None


class PluginHostRead(BaseModel):
    host: str
    purpose: dict[str, str]


class PluginScopeRead(BaseModel):
    scope: str
    purpose: dict[str, str]


class PluginThemeRead(BaseModel):
    key: str
    name: dict[str, str]
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    radius: float | None = None
    font: Literal["sans", "mono", "serif"] | None = None


class InstalledPluginRead(BaseModel):
    """A catalog plugin installed in this workspace, as recorded here: no catalog needed."""

    plugin_id: str
    name: dict[str, str]
    version: str
    commit: str
    installed_at: datetime
    integrations: list[PluginIntegrationRead] = Field(default=[], description="For plugins with code.")
    network: list[PluginHostRead] = []
    api_scopes: list[PluginScopeRead] = []
    redirect_uri: str | None = Field(
        default=None, description="For sign-in integrations: the address to register with the service."
    )
    themes: list[PluginThemeRead] = Field(default=[], description="Themes it adds to the library.")


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
    integrations: list[PluginIntegrationRead] = []
    network: list[PluginHostRead] = []
    api_scopes: list[PluginScopeRead] = []
    themes: list[PluginThemeRead] = []


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
    code: "PluginCodeConsentRead | None" = Field(
        default=None, description="For plugins with code: what the owner agrees to let it do."
    )
    themes: "PluginThemeChangesRead | None" = Field(
        default=None, description="Themes the action adds to the library and takes out of it."
    )


class PluginThemeNameRead(BaseModel):
    key: str
    name: dict[str, str]


class PluginThemeChangesRead(BaseModel):
    added: list[PluginThemeNameRead] = []
    removed: list[PluginThemeNameRead] = Field(
        default=[], description="Members using one of these go back to Securo's own theme."
    )


class PluginCodeIntegrationRead(BaseModel):
    key: str
    name: dict[str, str]
    auth: Literal["credentials", "oauth", "none"]


class PluginCodeConsentRead(BaseModel):
    network: list[PluginHostRead]
    api_scopes: list[PluginScopeRead]
    integrations: list[PluginCodeIntegrationRead]
    new_hosts: list[str] = Field(description="On an update: hosts the installed version couldn't reach.")
    new_scopes: list[str]
    connections: int = Field(description="Connections in this workspace; removed along with the plugin.")
    drops_code: bool = Field(default=False, description="An update to a version without code: connections go too.")


class PluginConnectionRead(BaseModel):
    """A connection made through a plugin. Its secrets are never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plugin_id: str
    integration: str
    name: str
    status: Literal["pending", "active", "error", "needs_auth"]
    config: dict[str, Any]
    secret_fields: list[str] = Field(default=[], description="Secret fields that are set (names only).")
    sync_minutes: int | None
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime


class PluginConnectionCreate(BaseModel):
    plugin_id: str = Field(max_length=120)
    integration: str = Field(max_length=64)
    name: str = Field(default="", max_length=100)
    values: dict[str, Any] = {}


class PluginConnectionCreated(BaseModel):
    connection: PluginConnectionRead
    authorize_url: str | None = Field(description="For sign-in integrations: send the browser here.")


class PluginAuthorizeRead(BaseModel):
    authorize_url: str
    redirect_uri: str


class PluginOAuthCallback(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=256)


class PluginApplyRequest(BaseModel):
    action: Literal["install", "update", "remove"]
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    token: str = Field(min_length=64, max_length=64)


PluginPlanRead.model_rebuild()
