from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field(..., description="ollama|openai|anthropic|openai_compatible")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class ConnectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    base_url: Optional[str] = None
    # None = leave unchanged; empty string = clear; non-empty = replace
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None


class ConnectionRead(BaseModel):
    """Note: api_key is NEVER returned. The presence of credentials is
    indicated by `has_api_key`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    base_url: Optional[str]
    default_model: Optional[str]
    extra: dict[str, Any]
    is_default: bool
    has_api_key: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, row) -> "ConnectionRead":
        return cls(
            id=row.id,
            name=row.name,
            kind=row.kind,
            base_url=row.base_url,
            default_model=row.default_model,
            extra=row.extra or {},
            is_default=bool(row.is_default),
            has_api_key=bool(row.api_key_encrypted),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ConnectionTestResult(BaseModel):
    ok: bool
    detail: str
    models: Optional[list[str]] = None
