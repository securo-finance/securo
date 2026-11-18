from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    ordinal: int
    content: Optional[str]
    tool_calls: Optional[list[Any]]
    tool_result: Optional[dict[str, Any]]
    citations: Optional[list[Any]]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    channel: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime


class SendMessageRequest(BaseModel):
    # A message longer than this is a mistake or an attempt to burn provider
    # tokens, and either way it is refused before the stream opens.
    content: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[uuid.UUID] = None
    channel: str = "web"
    # Where the user is in the app when this message was sent. The
    # executor injects a short system message so the agent can answer
    # context-aware questions like "what about THIS row?". Format is
    # free-form — the frontend builds it from the active page.
    page_context: Optional[dict[str, Any]] = None
