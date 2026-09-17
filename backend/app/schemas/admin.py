import uuid
from typing import Optional

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


class AppSettingRead(BaseModel):
    key: str
    value: str

    model_config = ConfigDict(from_attributes=True)


class AppSettingUpdate(BaseModel):
    value: str


class TimezoneSettingRead(BaseModel):
    timezone: str
    available: list[str]
