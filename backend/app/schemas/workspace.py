import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    is_archived: bool
    default_currency: str
    locale: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    created_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    managed_by_user_id: Optional[uuid.UUID] = None
    # The current user's role inside this workspace, when surfaced via
    # /api/workspaces (the listing endpoint). Omitted from per-workspace
    # detail responses since the membership row is fetched alongside.
    role: Optional[str] = None

    class Config:
        from_attributes = True


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = "personal"
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    # When True, also add the creator as an `owner` member. When False
    # (default), the creator is only the external manager — useful when
    # the workspace will be handed off to someone else as the day-to-day
    # owner.
    self_membership: bool = False


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)


class MemberRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: Optional[str] = None
    role: str
    joined_at: datetime

    class Config:
        from_attributes = True


class MemberInvite(BaseModel):
    email: EmailStr
    role: str = "editor"
    # Optional password — only used when inviting a brand-new user. If
    # omitted, the endpoint rejects the invite when the target user
    # doesn't exist. (Email-based magic-link onboarding can come later.)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class MemberRoleUpdate(BaseModel):
    role: str
