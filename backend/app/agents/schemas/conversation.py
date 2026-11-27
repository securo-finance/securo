from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


PAGE_CONTEXT_MAX_CHARS = 4000


class SendMessageRequest(BaseModel):
    # A message longer than this is a mistake or an attempt to burn provider
    # tokens, and either way it is refused before the stream opens.
    content: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[uuid.UUID] = None
    # Capped at the width of the column it is stored in: an over-long channel
    # was a database error rather than a refusal.
    channel: str = Field("web", min_length=1, max_length=40)
    # Where the user is in the app when this message was sent. The
    # executor injects a short system message so the agent can answer
    # context-aware questions like "what about THIS row?". Format is
    # free-form — the frontend builds it from the active page — but its size
    # is bounded: the message cap above means nothing if the blob that
    # travels next to it into the same prompt is unbounded.
    page_context: Optional[dict[str, Any]] = None

    @field_validator("page_context")
    @classmethod
    def _page_context_fits_the_prompt(
        cls, value: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        if value is None:
            return value
        size = len(json.dumps(value, default=str, ensure_ascii=False))
        if size > PAGE_CONTEXT_MAX_CHARS:
            raise ValueError(
                f"page_context is {size} characters once serialized, above the "
                f"{PAGE_CONTEXT_MAX_CHARS} allowed"
            )
        return value
