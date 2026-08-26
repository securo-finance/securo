import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BackupContent(StrEnum):
    configuration = "configuration"
    data = "data"
    both = "both"


class BackupRestoreMode(StrEnum):
    new_workspace = "new_workspace"
    current_workspace = "current_workspace"


class BackupConfig(BaseModel):
    scheduled_enabled: bool = False
    schedule: Literal["daily", "weekly"] = "daily"
    content: BackupContent = BackupContent.both
    retention_count: int = Field(default=10, ge=1, le=200)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class BackupConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_enabled: bool | None = None
    schedule: Literal["daily", "weekly"] | None = None
    content: BackupContent | None = None
    retention_count: int | None = Field(default=None, ge=1, le=200)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class BackupRunRequest(BaseModel):
    content: BackupContent | None = None


class BackupItem(BaseModel):
    id: str
    filename: str
    size_bytes: int
    created_at: datetime
    workspace_id: uuid.UUID | None = None
    workspace_name: str | None = None
    content: BackupContent = BackupContent.both
    entity_counts: dict[str, int] = Field(default_factory=dict)


class BackupPreview(BaseModel):
    valid: bool
    format_version: str | None = None
    export_date: datetime | None = None
    workspace_id: uuid.UUID | None = None
    workspace_name: str | None = None
    content: BackupContent = BackupContent.both
    entity_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BackupRestoreRequest(BaseModel):
    content: BackupContent = BackupContent.both
    mode: BackupRestoreMode = BackupRestoreMode.new_workspace
    confirmation: str | None = None


class BackupRestoreResult(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    mode: BackupRestoreMode
    content: BackupContent
    restored_counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
