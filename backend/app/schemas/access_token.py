import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AccessTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    # None means the token never expires.
    expires_in_days: Optional[int] = Field(default=90, ge=1, le=3650)


class AccessTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    is_active: bool


class AccessTokenCreated(AccessTokenRead):
    # The secret. Returned only once, at creation.
    token: str


class AccessTokenScopes(BaseModel):
    scopes: list[str]
