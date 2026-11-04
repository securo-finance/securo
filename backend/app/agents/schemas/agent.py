from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    system_prompt: str = ""
    icon: str = "bot"
    color: str = "#6B7280"
    connection_id: Optional[uuid.UUID] = Field(
        None,
        description="LlmConnection to use; falls back to instance default when null",
    )
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: float = Field(0.4, ge=0.0, le=2.0)
    max_history_messages: int = Field(20, ge=1, le=200)
    top_n: int = Field(6, ge=0, le=50)
    similarity_threshold: float = Field(0.25, ge=0.0, le=1.0)
    extra: dict[str, Any] = Field(default_factory=dict)
    auto_context: bool = True
    is_default: bool = False
    # Optional tool whitelist written in the same transaction as the agent,
    # so an agent created with a tool scope never exists without it. Left
    # out (None), the agent keeps the allow-all default.
    tools: Optional[list["AgentToolToggle"]] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    connection_id: Optional[uuid.UUID] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_history_messages: Optional[int] = Field(None, ge=1, le=200)
    top_n: Optional[int] = Field(None, ge=0, le=50)
    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    extra: Optional[dict[str, Any]] = None
    auto_context: Optional[bool] = None
    is_archived: Optional[bool] = None
    is_default: Optional[bool] = None


class AgentToolToggle(BaseModel):
    server: str
    tool_name: str
    enabled: bool


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str]
    system_prompt: str
    icon: str
    color: str
    connection_id: Optional[uuid.UUID]
    provider: Optional[str]
    model: Optional[str]
    temperature: float
    max_history_messages: int
    top_n: int
    similarity_threshold: float
    extra: dict[str, Any]
    auto_context: bool = True
    is_archived: bool
    is_default: bool = False
    # Convenience counts populated by list_agents so the agents list
    # page can display them without N+1 round-trips. Defaults to 0 so
    # single-agent endpoints (which don't compute these) still validate.
    conversation_count: int = 0
    knowledge_count: int = 0
    created_at: datetime
    updated_at: datetime
