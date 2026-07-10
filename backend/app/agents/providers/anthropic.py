from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import httpx

from app.agents.providers.base import (
    ChatChunk,
    ChatMessage,
    LLMAuthError,
    LLMNotSupportedError,
    LLMProvider,
    LLMRateLimitError,
    LLMUnavailableError,
    ToolDefinition,
    Usage,
)


def _split_system(messages: list[ChatMessage]) -> tuple[Optional[str], list[ChatMessage]]:
    sys: list[str] = []
    rest: list[ChatMessage] = []
    for m in messages:
        if m.role == "system" and m.content:
            sys.append(m.content)
        else:
            rest.append(m)
    return ("\n\n".join(sys) if sys else None), rest


def _serialize_messages(messages: list[ChatMessage]) -> list[dict]:
    """Anthropic Messages API: alternating user/assistant, tool results
    are user-role messages with content blocks."""
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": m.content or "",
                        }
                    ],
                }
            )
            continue
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": m.role, "content": m.content or ""})
    return out


def _serialize_tools(tools: Optional[list[ToolDefinition]]) -> Optional[list[dict]]:
    if not tools:
        return None
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]


def _raise_for_status(status: int, body: str) -> None:
    if status in (401, 403):
        raise LLMAuthError(f"Anthropic auth failed: {body}", status=status)
    if status == 429:
        raise LLMRateLimitError(f"Anthropic rate limit: {body}", status=status)
    if status >= 500:
        raise LLMUnavailableError(f"Anthropic {status}: {body}", status=status)
    if status >= 400:
        raise LLMUnavailableError(f"Anthropic {status}: {body}", status=status)


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_embeddings = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        model: Optional[str] = None,
    ):
        super().__init__(api_key=api_key, base_url=base_url, model=model)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.4,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[ChatChunk]:
        url = f"{self.base_url.rstrip('/')}/messages"
        system, rest = _split_system(messages)
        payload: dict = {
            "model": model,
            "messages": _serialize_messages(rest),
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _serialize_tools(tools)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        _raise_for_status(resp.status_code, body)
                    open_block: dict = {}
                    usage = Usage()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        evt = json.loads(data_str)
                        et = evt.get("type")
                        if et == "content_block_start":
                            block = evt.get("content_block") or {}
                            idx = evt.get("index", 0)
                            if block.get("type") == "tool_use":
                                tcid = block.get("id") or f"toolu_{idx}"
                                open_block[idx] = {
                                    "kind": "tool",
                                    "id": tcid,
                                    "name": block.get("name") or "",
                                }
                                yield ChatChunk(
                                    type="tool_call_start",
                                    tool_call_id=tcid,
                                    tool_name=open_block[idx]["name"],
                                )
                            elif block.get("type") == "text":
                                open_block[idx] = {"kind": "text"}
                        elif et == "content_block_delta":
                            idx = evt.get("index", 0)
                            delta = evt.get("delta") or {}
                            blk = open_block.get(idx) or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield ChatChunk(type="text_delta", text=delta["text"])
                            elif (
                                delta.get("type") == "input_json_delta"
                                and blk.get("kind") == "tool"
                            ):
                                yield ChatChunk(
                                    type="tool_call_args_delta",
                                    tool_call_id=blk["id"],
                                    args_delta=delta.get("partial_json") or "",
                                )
                        elif et == "content_block_stop":
                            idx = evt.get("index", 0)
                            blk = open_block.pop(idx, None)
                            if blk and blk.get("kind") == "tool":
                                yield ChatChunk(type="tool_call_end", tool_call_id=blk["id"])
                        elif et == "message_delta":
                            u = evt.get("usage") or {}
                            usage = Usage(
                                input_tokens=usage.input_tokens or int(u.get("input_tokens") or 0),
                                output_tokens=int(u.get("output_tokens") or usage.output_tokens),
                            )
                            stop = (evt.get("delta") or {}).get("stop_reason")
                            if stop:
                                yield ChatChunk(type="usage", usage=usage)
                                yield ChatChunk(type="finish", finish_reason=stop)
                        elif et == "message_start":
                            u = (evt.get("message") or {}).get("usage") or {}
                            usage = Usage(
                                input_tokens=int(u.get("input_tokens") or 0),
                                output_tokens=int(u.get("output_tokens") or 0),
                            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Anthropic unreachable: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMNotSupportedError("Anthropic does not provide an embeddings endpoint")
