import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

REASON_MIN_LENGTH = 3
REASON_MAX_LENGTH = 500


def clean_reason(value: str) -> str:
    """The reason as it is stored: trimmed, and never blank or unstorable.

    The length is measured after trimming, so a reason of spaces is refused
    the same way an empty one is — the screen already disables the button for
    it, and the API must not accept what the screen refuses. PostgreSQL text
    cannot hold a NUL byte, so one reaching the driver answered 500 instead of
    refusing the input (#557).
    """
    cleaned = value.strip()
    if not REASON_MIN_LENGTH <= len(cleaned) <= REASON_MAX_LENGTH:
        raise ValueError(
            f"Reason must be between {REASON_MIN_LENGTH} and "
            f"{REASON_MAX_LENGTH} characters"
        )
    if "\x00" in cleaned:
        raise ValueError("Reason cannot contain NUL characters")
    return cleaned


#: A reason, in the body of an update and in the query of a deletion (#557).
AdminReason = Annotated[str, AfterValidator(clean_reason)]


class AdminUserRead(BaseModel):
    id: uuid.UUID
    email: str
    #: An address an administrator asked for, waiting for the account's owner
    #: to accept it (#556). The login address stays ``email`` until then.
    pending_email: Optional[str] = None
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
    reason: AdminReason
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
    #: Every field the action changed, old value and new (#556).
    changes: Optional[dict] = None
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
