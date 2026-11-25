import uuid
from datetime import datetime
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
    """What an administrator may change on someone's account.

    There is no password here on purpose (ADR 0042): a password only ever
    belongs to the person it authenticates. Unknown fields are refused, so an
    older client that still sends ``password`` gets a 422 instead of silently
    having it dropped.
    """

    model_config = ConfigDict(extra="forbid")

    #: Why the account is being touched. Required, and logged with the change.
    reason: str = Field(min_length=3, max_length=500)
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    preferences: Optional[dict] = None


class AdminUserList(BaseModel):
    items: list[AdminUserRead]
    total: int


class AdminAccessLogRead(BaseModel):
    """One administrative action on an account, as the affected side sees it."""

    id: uuid.UUID
    actor_email: str
    target_email: str
    target_user_id: Optional[uuid.UUID] = None
    action: str
    reason: str
    ip: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminAccessLogList(BaseModel):
    items: list[AdminAccessLogRead]
    total: int


class AppSettingRead(BaseModel):
    key: str
    value: str

    model_config = ConfigDict(from_attributes=True)


class AppSettingUpdate(BaseModel):
    value: str


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
