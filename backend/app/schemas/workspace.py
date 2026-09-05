import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.models.workspace import WorkspaceKind


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    # Deliberately a bare `str` on the way out: reads must not blow up on
    # a row that predates the current kind list. Writes are validated.
    kind: str
    is_archived: bool
    default_currency: str
    locale: Optional[str] = None
    # Where the workspace operates fiscally. Never the UI language: see
    # `models.workspace.Workspace.tax_jurisdiction`.
    tax_jurisdiction: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    created_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    managed_by_user_id: Optional[uuid.UUID] = None
    # The current user's role inside this workspace, when surfaced via
    # /api/workspaces (the listing endpoint). Omitted from per-workspace
    # detail responses since the membership row is fetched alongside.
    role: Optional[str] = None
    # Which modules this workspace shows, resolved server-side by
    # `services.module_service`. The frontend consumes this list; it must
    # never re-derive it from `kind`, or the two copies drift and a user
    # sees a module the server thinks is off.
    enabled_modules: list[str] = []

    class Config:
        from_attributes = True


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: WorkspaceKind = "personal"
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)
    # Configuration, unlike `kind`: a business can move, and a workspace set
    # up before packs existed needs a way to say where it files.
    tax_jurisdiction: Optional[str] = Field(default=None, max_length=10)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    # When True, also add the creator as an `owner` member. When False
    # (default), the creator is only the external manager — useful when
    # the workspace will be handed off to someone else as the day-to-day
    # owner.
    self_membership: bool = False


class WorkspaceUpdate(BaseModel):
    # No `kind` here on purpose: it is fixed at creation. See
    # `models.workspace.WorkspaceKind`.
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    icon: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=7)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    locale: Optional[str] = Field(default=None, max_length=10)
    # Editable, unlike `kind`: a business relocates, and every workspace that
    # existed before jurisdictions did needs a way to say where it files.
    tax_jurisdiction: Optional[str] = Field(default=None, max_length=10)


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
