import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PersonalAccessTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    read_only: bool = False
    # None means the token never expires.
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class PersonalAccessTokenRead(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str
    workspace_id: uuid.UUID
    workspace_name: str
    read_only: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime


class PersonalAccessTokenCreated(PersonalAccessTokenRead):
    # The plaintext secret. Returned only in the create response.
    token: str
