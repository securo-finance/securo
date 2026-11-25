import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminUserRead(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    preferences: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    is_superuser: bool = False
    preferences: Optional[dict] = None


class AdminUserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8, max_length=72)
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    preferences: Optional[dict] = None


class AdminUserList(BaseModel):
    items: list[AdminUserRead]
    total: int


class InstanceModeRead(BaseModel):
    """What the admin screen may change on this instance (ADR 0045)."""

    managed: bool
    locked_settings: list[str]
    locked_providers: bool


class AppSettingRead(BaseModel):
    key: str
    value: str

    model_config = ConfigDict(from_attributes=True)


class AppSettingUpdate(BaseModel):
    value: str


class EmailTemplate(BaseModel):
    """What goes into the recovery message, with its placeholders kept."""

    subject: str
    body: str


class EmailStateRead(BaseModel):
    """How outbound email is set up, and the templates it would use."""

    enabled: bool
    provider: Literal["smtp", "resend"]
    sender: str
    templates: dict[str, EmailTemplate]


class ProviderFieldStatus(BaseModel):
    configured: bool
    source: Literal["app", "environment", "none"]
    invalid: bool
    environment_configured: bool


class ProviderSettingRead(BaseModel):
    """Credential values and ciphertext are intentionally excluded."""

    name: str
    configured: bool
    can_store_secrets: bool
    fields: dict[str, ProviderFieldStatus]
    #: Callback URL to register with the provider, when it uses one.
    redirect_uri: Optional[str] = None


class TimezoneSettingRead(BaseModel):
    #: The timezone in use, after fallbacks.
    timezone: str
    #: What an administrator saved, valid or not; None when nothing is saved.
    saved: Optional[str] = None
    #: Where the application lands without a saved value (TZ, host, UTC).
    fallback: str
    available: list[str]
