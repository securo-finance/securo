import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

GroupKind = Literal["social", "cost_center", "project", "client", "other"]


class GroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: GroupKind = "social"
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    icon: str = "users"
    color: str = "#6B7280"
    notes: Optional[str] = None


class GroupCreate(GroupBase):
    pass


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    kind: Optional[GroupKind] = None
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    icon: Optional[str] = None
    color: Optional[str] = None
    notes: Optional[str] = None
    is_archived: Optional[bool] = None


class GroupMemberBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    linked_user_id: Optional[uuid.UUID] = None
    email: Optional[EmailStr] = None
    is_self: bool = False


class GroupMemberCreate(GroupMemberBase):
    pass


class GroupMemberUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    linked_user_id: Optional[uuid.UUID] = None
    email: Optional[EmailStr] = None
    is_self: Optional[bool] = None


class GroupMemberRead(GroupMemberBase):
    id: uuid.UUID
    group_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GroupRead(GroupBase):
    id: uuid.UUID
    user_id: uuid.UUID
    is_archived: bool
    # Derived per-request: True if the requester is the group owner.
    # Linked members get False — the frontend uses this to hide edit UI.
    is_owner: bool = True
    created_at: datetime
    members: list[GroupMemberRead] = []

    model_config = ConfigDict(from_attributes=True)


class GroupBalanceLine(BaseModel):
    member_id: uuid.UUID
    currency: str
    # Positive = member owes the group owner. Negative = owner owes member.
    amount: Decimal
    # Same value FX-converted to the group's default currency, so the
    # frontend can roll up cross-currency lines into a single KPI.
    amount_in_default_currency: Decimal


class GroupBalances(BaseModel):
    group_id: uuid.UUID
    self_member_id: Optional[uuid.UUID] = None
    default_currency: str
    lines: list[GroupBalanceLine] = []


class GroupSummaryMonth(BaseModel):
    # "YYYY-MM"; the client formats it for display.
    month: str
    total: Decimal


class GroupSummaryCategory(BaseModel):
    # Null for transactions with no category.
    category_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    color: Optional[str] = None
    total: Decimal


class GroupSummary(BaseModel):
    group_id: uuid.UUID
    default_currency: str
    total_moved: Decimal
    months: list[GroupSummaryMonth] = []
    categories: list[GroupSummaryCategory] = []
