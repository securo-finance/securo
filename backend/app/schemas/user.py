import uuid
from typing import Optional

from fastapi_users import schemas
from pydantic import BaseModel


class UserPreferences(BaseModel):
    language: str = "en"
    date_format: str = "MM/DD/YYYY"
    currency_display: str = "USD"
    # Shortlist backing the dashboard quick-switch; empty means "not chosen
    # yet" and falls back to [currency_display].
    quick_currencies: list[str] = []
    onboarding_completed: bool = False


class UserRead(schemas.BaseUser[uuid.UUID]):
    preferences: Optional[dict] = None
    is_2fa_enabled: bool = False


class UserCreate(schemas.BaseUserCreate):
    preferences: Optional[dict] = None


class UserUpdate(schemas.BaseUserUpdate):
    preferences: Optional[dict] = None
