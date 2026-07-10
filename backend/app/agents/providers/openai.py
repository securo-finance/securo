from __future__ import annotations

import json
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from app.agents.providers.base import (
    ChatChunk,
    ChatMessage,
    LLMAuthError,
    LLMProvider,
    LLMRateLimitError,
    LLMUnavailableError,
    ToolDefinition,
    Usage,
)


def normalize_openai_base_url(url: str) -> str:
    """Ensure the URL points to an OpenAI-compatible /v1 root.

    Most local OpenAI-compatible servers (LM Studio, vLLM, Ollama's
    openai endpoint, etc.) live under `/v1/*`. Users typically paste
    just the host — `http://lmstudio:1234` — expecting the client to
    append `/v1`. Without this, requests hit `/chat/completions` and
    silently 404 (LM Studio in particular returns 200 with no body).

    Rules:
      - empty → returned as-is
      - trailing slashes stripped
      - if any path segment looks like a version (`v1`, `v2`, `beta`,
        `latest`, `api/v3`, etc.), leave the URL alone
      - otherwise, append `/v1`
    """
    if not url:
        return url
    url = url.rstrip("/")
    path_segments = [s for s in (urlparse(url).path or "").split("/") if s]
    for seg in path_segments:
        if (seg.startswith("v") and seg[1:].isdigit()) or seg in {"beta", "latest"}:
            return url
    return url + "/v1"


def _serialize_messages(messages: list[ChatMessage]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        msg: dict = {"role": m.role}
        if m.content is not None:
            msg["content"] = m.content
        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
        if m.role == "tool":
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
        out.append(msg)
    return out


def _serialize_tools(tools: Optional[list[ToolDefinition]]) -> Optional[list[dict]]:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def _raise_for_status(status: int, body: str) -> None:
    if status == 401 or status == 403:
        raise LLMAuthError(f"OpenAI auth failed: {body}", status=status)
    if status == 429:
        raise LLMRateLimitError(f"OpenAI rate limit: {body}", status=status)
    if status >= 500:
        raise LLMUnavailableError(f"OpenAI {status}: {body}", status=status)
    if status >= 400:
        raise LLMUnavailableError(f"OpenAI {status}: {body}", status=status)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: Optional[str] = None,
    ):
        # Normalize at construction so every URL helper sees a `/v1`-rooted base.
        super().__init__(api_key=api_key, base_url=normalize_openai_base_url(base_url), model=model)

    def _headers(self) -> dict[str, str]:
        # Only attach Authorization when we actually have a key. Many local
        # OpenAI-compatible servers (LM Studio, vLLM, Ollama's OpenAI mode)
        # don't require auth — and httpx refuses to send the literal
        # `Bearer ` (with trailing space) as an illegal header value.
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.4,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[ChatChunk]:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload: dict = {
            "model": model,
            "messages": _serialize_messages(messages),
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = _serialize_tools(tools)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        _raise_for_status(resp.status_code, body)
                    # OpenAI's streaming protocol uses `index` (an integer)
                    # as the stable key for tool calls across chunks. The
                    # first chunk for an index carries `id` and `name`;
                    # subsequent chunks only carry `arguments` deltas.
                    # We MUST key by index, not by id — otherwise later
                    # chunks (without id) get registered as new entries
                    # with empty names and the dispatcher fails with
                    # "unknown tool: ".
                    idx_state: dict[int, dict] = {}
                    finish_reason: Optional[str] = None
                    usage: Optional[Usage] = None
                    async for raw in resp.aiter_lines():
                        if not raw.startswith("data:"):
                            continue
                        chunk_str = raw[5:].strip()
                        if not chunk_str or chunk_str == "[DONE]":
                            continue
                        chunk = json.loads(chunk_str)
                        if chunk.get("usage"):
                            u = chunk["usage"]
                            usage = Usage(
                                input_tokens=int(u.get("prompt_tokens") or 0),
                                output_tokens=int(u.get("completion_tokens") or 0),
                            )
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield ChatChunk(type="text_delta", text=delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                idx = int(tc.get("index", 0))
                                fn = tc.get("function") or {}
                                state = idx_state.setdefault(
                                    idx,
                                    {
                                        "id": None,
                                        "name": "",
                                        "started": False,
                                        "pending_args": "",
                                    },
                                )
                                # First chunk usually carries both id and name;
                                # some servers split the name across chunks.
                                if tc.get("id") and not state["id"]:
                                    state["id"] = tc["id"]
                                if fn.get("name"):
                                    state["name"] += fn["name"]
                                # Emit start exactly once, when we have enough
                                # info to dispatch (name is the must-have).
                                if not state["started"] and state["name"]:
                                    if not state["id"]:
                                        state["id"] = f"_idx_{idx}"
                                    state["started"] = True
                                    yield ChatChunk(
                                        type="tool_call_start",
                                        tool_call_id=state["id"],
                                        tool_name=state["name"],
                                    )
                                    if state["pending_args"]:
                                        yield ChatChunk(
                                            type="tool_call_args_delta",
                                            tool_call_id=state["id"],
                                            args_delta=state["pending_args"],
                                        )
                                        state["pending_args"] = ""
                                if fn.get("arguments"):
                                    if state["started"]:
                                        yield ChatChunk(
                                            type="tool_call_args_delta",
                                            tool_call_id=state["id"],
                                            args_delta=fn["arguments"],
                                        )
                                    else:
                                        # Args arrived before name (rare).
                                        state["pending_args"] += fn["arguments"]
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                    for state in idx_state.values():
                        if state["started"]:
                            yield ChatChunk(type="tool_call_end", tool_call_id=state["id"])
                    if usage:
                        yield ChatChunk(type="usage", usage=usage)
                    yield ChatChunk(type="finish", finish_reason=finish_reason or "stop")
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"OpenAI unreachable: {exc}") from exc

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        url = f"{self.base_url.rstrip('/')}/embeddings"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    url, json={"model": model, "input": texts}, headers=self._headers()
                )
                if resp.status_code >= 400:
                    _raise_for_status(resp.status_code, resp.text)
                data = resp.json()
                return [item["embedding"] for item in data.get("data", [])]
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"OpenAI unreachable: {exc}") from exc
