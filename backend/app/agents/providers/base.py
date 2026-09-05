from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Optional


# --- Errors ---------------------------------------------------------------

class LLMError(Exception):
    """Base class for provider failures. Subclasses let the runtime show
    the user a friendly message instead of a stack trace."""

    code: str = "unknown"

    def __init__(self, message: str, *, retryable: bool = False, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMAuthError(LLMError):
    code = "auth"


class LLMRateLimitError(LLMError):
    code = "rate_limit"

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message, retryable=True, status=status)


class LLMUnavailableError(LLMError):
    code = "unavailable"

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message, retryable=True, status=status)


class LLMNotSupportedError(LLMError):
    code = "not_supported"


# --- Messages, tools, chunks ---------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    role: Role
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # For role="tool":
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ToolDefinition:
    """Provider-agnostic tool spec. Matches MCP/JSON-Schema shape."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments object


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatChunk:
    """Streaming event. Exactly one of these fields is populated per chunk."""
    type: Literal["text_delta", "tool_call_start", "tool_call_args_delta", "tool_call_end", "finish", "usage"]
    text: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    args_delta: Optional[str] = None  # incremental JSON args while streaming
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)


# --- Provider interface ---------------------------------------------------

class LLMProvider(ABC):
    name: str
    supports_native_tools: bool = True
    # Embedding capability — Anthropic returns False, others True.
    supports_embeddings: bool = True

    def __init__(self, *, api_key: str = "", base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.base_url: str = base_url or ""
        self.default_model = model

    @abstractmethod
    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.4,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[ChatChunk]:
        """Yield streaming chunks. Implementations are async generators."""
        ...

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.4,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        """Convenience: collect a streaming response into one ChatResponse."""
        text_parts: list[str] = []
        tool_calls: dict[str, dict] = {}
        finish: str = "stop"
        usage = Usage()
        async for chunk in self.chat_stream(
            messages, model=model, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            if chunk.type == "text_delta" and chunk.text:
                text_parts.append(chunk.text)
            elif chunk.type == "tool_call_start" and chunk.tool_call_id:
                tool_calls[chunk.tool_call_id] = {"id": chunk.tool_call_id, "name": chunk.tool_name or "", "args_buf": ""}
            elif chunk.type == "tool_call_args_delta" and chunk.tool_call_id:
                tc = tool_calls.setdefault(chunk.tool_call_id, {"id": chunk.tool_call_id, "name": chunk.tool_name or "", "args_buf": ""})
                tc["args_buf"] += chunk.args_delta or ""
            elif chunk.type == "finish" and chunk.finish_reason:
                finish = chunk.finish_reason
            elif chunk.type == "usage" and chunk.usage:
                usage = chunk.usage
        import json
        parsed_calls = []
        for tc in tool_calls.values():
            try:
                args = json.loads(tc["args_buf"]) if tc["args_buf"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["args_buf"]}
            parsed_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
        return ChatResponse(content="".join(text_parts), tool_calls=parsed_calls, finish_reason=finish, usage=usage)

    @abstractmethod
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        ...
