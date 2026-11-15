import uuid
from typing import Optional

from fastapi_users import schemas
from pydantic import BaseModel, Field

#: A theme from the library: a built-in id (`ocean`) or, later, a plugin's
#: (`plugin:owner/name:key`). The frontend owns the registry and falls back to
#: Securo's own look for an id it doesn't know.
THEME_PATTERN = r"^[a-z0-9][a-z0-9:/_.-]{0,159}$"


class UserPreferences(BaseModel):
    language: str = "en"
    date_format: str = "MM/DD/YYYY"
    currency_display: str = "USD"
    onboarding_completed: bool = False


class UserRead(schemas.BaseUser[uuid.UUID]):
    preferences: Optional[dict] = None
    theme: Optional[str] = None
    is_2fa_enabled: bool = False


class UserCreate(schemas.BaseUserCreate):
    preferences: Optional[dict] = None


class UserUpdate(schemas.BaseUserUpdate):
    preferences: Optional[dict] = None
    theme: Optional[str] = Field(default=None, pattern=THEME_PATTERN)
