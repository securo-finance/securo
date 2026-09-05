"""Channel-agnostic agent execution loop.

Takes (agent, user_id, conversation_id, user_message) and yields a stream
of structured events. Whether the caller is the web SSE endpoint or a
future WhatsApp gateway, this loop is the same.

Flow:
  1. Load conversation history from DB.
  2. Discover tools from MCP servers; filter by per-agent whitelist.
  3. Loop:
     a. Stream LLM response.
     b. If LLM emits tool calls, run them in parallel against MCP and
        feed results back as `role=tool` messages, then continue.
     c. Stop when finish_reason is `stop` (no tool call this turn).
  4. Persist user + assistant messages and any tool messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Optional, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import get_agent_settings
from app.agents.mcp.client import MCPRegistry
from app.agents.models.agent import Agent
from app.agents.providers.base import (
    ChatChunk,
    ChatMessage,
    LLMAuthError,
    LLMError,
    LLMNotSupportedError,
    LLMRateLimitError,
    LLMUnavailableError,
    Role,
    ToolCall,
)
from app.agents.providers.registry import build_provider
from app.agents.services import agent_service, context_service, conversation_service, usage_service

logger = logging.getLogger(__name__)


@dataclass
class ExecutorEvent:
    type: Literal[
        "text_delta",
        "tool_call",      # tool name + args (after assembly)
        "tool_result",    # tool name + ok + summary
        "citation",
        "error",
        "done",
    ]
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    tool_result: Optional[dict[str, Any]] = None
    citation: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    finish_reason: Optional[str] = None


def _provider_for(agent: Agent):
    """Build a provider from env-var defaults (no connection lookup).

    Kept as a separate function so tests can monkey-patch this single
    seam to inject a scripted provider. Production goes through
    `_provider_and_model_for` which prefers user-managed connections.
    """
    name = agent.provider or os.getenv("AGENTS_DEFAULT_PROVIDER", "ollama")
    api_key = ""
    base_url = None
    if name == "openai":
        api_key = os.getenv("AGENTS_OPENAI_API_KEY", "")
    elif name == "anthropic":
        api_key = os.getenv("AGENTS_ANTHROPIC_API_KEY", "")
    elif name == "ollama":
        base_url = os.getenv("AGENTS_OLLAMA_BASE_URL", "http://ollama:11434")
    elif name == "openai_compatible":
        api_key = os.getenv("AGENTS_OPENAI_COMPAT_API_KEY", "")
        base_url = os.getenv("AGENTS_OPENAI_COMPAT_BASE_URL")
    return build_provider(name, api_key=api_key, base_url=base_url, model=agent.model)


def _model_for(agent: Agent) -> str:
    if agent.model:
        return agent.model
    return os.getenv("AGENTS_DEFAULT_MODEL", "")


async def _provider_and_model_for(session, agent: Agent):
    """Resolve (provider, model) using this priority:
        1. agent.connection_id        — explicit user-managed connection
        2. user's default connection  — is_default=True
        3. _provider_for(agent)       — env-var fallback (testable seam)

    Returns (LLMProvider, model_id_str).
    """
    from app.agents.services import connection_service  # local import: cycle safety

    conn = None
    if agent.connection_id:
        conn = await connection_service.get_connection(session, agent.connection_id, agent.user_id)
    if conn is None:
        conn = await connection_service.get_default_connection(session, agent.user_id)

    if conn is not None:
        provider = connection_service.build_provider_for_connection(conn)
        model = agent.model or conn.default_model or os.getenv("AGENTS_DEFAULT_MODEL", "")
        return provider, model

    return _provider_for(agent), _model_for(agent)


_RUNTIME_GUARDRAIL = (
    "## Runtime rules (always apply, regardless of the agent's own prompt)\n"
    "\n"
    "1. Tools whose name starts with `propose_` are PREVIEWS, not actions. "
    "Calling them does NOT change the user's data. They return a structured "
    "proposal and the UI renders an Apply button. After calling one, "
    "describe it as a proposal — say 'I prepared a proposal…' or 'Here's a "
    "preview…'. NEVER say 'I created', 'I added', 'Done', 'Ready', or "
    "anything that implies the action has been executed.\n"
    "\n"
    "2. Don't silently substitute entities the user named. If the user "
    "asks for a category/account/payee/group 'X' and the lookup returns "
    "no match, you MUST stop and tell the user before doing anything else. "
    "Never quietly pick a similar-looking one (e.g. user said 'Coleguinhas', "
    "only 'Amigos' exists → STOP, ask, do not build a proposal against "
    "'Amigos'). For categories specifically: if the user-named category "
    "doesn't exist, prefer leaving `category_id` null in the transaction "
    "proposal and mention it in your reply — don't chain a "
    "propose_create_category step unless the user explicitly asked to "
    "create the category.\n"
    "\n"
    "3. The auto-context primer (when present) is orientation only — never "
    "quote balances or counts from it; query the tools for live numbers.\n"
    "\n"
    "4. When responding in Portuguese (or any non-English language), keep "
    "your phrasing language-consistent — don't mix English snippets like "
    "'I prepared a proposal' into a Portuguese reply. Use 'Preparei uma "
    "proposta…' / 'Aqui está uma prévia…'.\n"
    "\n"
    "5. Charts: when a visualization would clearly help (trends over "
    "time, category breakdowns, comparisons), render one inline by "
    "emitting a fenced code block tagged `securo-chart`. The body is "
    "JSON. Multi-series example:\n"
    "\n"
    "```securo-chart\n"
    "{\n"
    '  "type": "line",            // line | bar | area | pie\n'
    '  "title": "Income vs expenses (last 6 months)",\n'
    '  "currency": "BRL",         // optional, formats Y axis as money\n'
    '  "data": [\n'
    '    {"x": "Jan", "income": 3200, "expense": 2100},\n'
    '    {"x": "Feb", "income": 3400, "expense": 2300}\n'
    "  ],\n"
    '  "series": [\n'
    '    {"key": "income",  "name": "Income"},\n'
    '    {"key": "expense", "name": "Expense"}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "\n"
    "Single-series shorthand: omit `series` and use the key `y`. "
    'Example: `{\"type\":\"bar\",\"data\":[{\"x\":\"Food\",\"y\":500},'
    '{\"x\":\"Rent\",\"y\":1200}]}`. For pie use '
    '`{\"type\":\"pie\",\"data\":[{\"name\":\"Food\",\"value\":500},...]}`.\n'
    "\n"
    "Add a one-sentence summary above the chart; do NOT also list every "
    "data point in prose — let the chart speak. Only render charts when "
    "you have at least 2 data points to show.\n"
    "\n"
    "6. Knowledge base: if `search_knowledge_base` is available, the user "
    "has uploaded reference documents to this agent. Treat the KB as the "
    "authoritative source for anything that is NOT plainly transactional "
    "data in the user's accounts — including laws, regulations, tax rules, "
    "accounting standards, contracts, internal policies, project briefs, "
    "team/people names, internal codes, identifiers, dates, addresses, "
    "definitions, jargon, or any domain-specific knowledge.\n"
    "\n"
    "   - You MUST call `search_knowledge_base` before any answer that "
    "claims something is or isn't in the documents. Phrases like 'não "
    "encontrei nos documentos', 'I didn't find this in the docs', or 'isso "
    "não consta' are forbidden unless you actually called the tool first "
    "and got no relevant result. Pre-judging without searching is a bug, "
    "even if you think the question is out of scope.\n"
    "   - Use the user's own wording as the query (translate if needed). "
    "Try a second query with related terms if the first returns nothing.\n"
    "   - If the KB returns relevant chunks, answer from them and cite the "
    "doc title or filename inline (e.g. 'segundo o briefing X…').\n"
    "   - If the KB returns nothing relevant (no items, or only low-score "
    "chunks unrelated to the question), DO NOT invent an answer and DO NOT "
    "fall back to general world knowledge as if it were authoritative. "
    "Say plainly that you searched and didn't find this in the uploaded "
    "documents, and offer to look at it differently if the user can share "
    "a doc or rephrase. It's better to say 'I don't know' than to guess.\n"
    "\n"
    "7. Trust the tools' arithmetic. When `aggregate` or any other server-"
    "side total returns a value, that IS the answer — quote it directly. "
    "NEVER list the individual transactions and re-sum them by hand: model "
    "arithmetic over long lists is unreliable and will drift from the SQL "
    "result. If you need a keyword filter (merchant name, payee), pass "
    "`description_contains` to `aggregate` rather than listing and "
    "summing. The only acceptable place to do arithmetic yourself is a "
    "single combine step on tool outputs (e.g. `1847 * 12` = 22164).\n"
)


def _format_page_context(page_context: Optional[dict[str, Any]]) -> Optional[str]:
    """Render the frontend's page-context blob as a short system message.

    The frontend builds a free-form dict — typically:
      {"path": "/transactions", "label": "Transactions",
       "filters": {...}, "selection": {...}, "summary": "..."}
    We render it as a compact bullet list so the model can parse it but
    the cost stays small. Empty/None values are dropped.
    """
    if not page_context:
        return None
    path = page_context.get("path") or page_context.get("route")
    label = page_context.get("label") or path
    lines = ["## Current page context",
             "The user is sending this message from the page below — when they refer "
             "to 'this', 'these', 'aqui', etc., it most likely refers to what's on "
             "this page right now."]
    if label or path:
        lines.append(f"- **Page:** {label or '?'}{f' ({path})' if (path and label != path) else ''}")
    summary = page_context.get("summary")
    if summary:
        lines.append(f"- **Summary:** {summary}")
    filters = page_context.get("filters")
    if filters:
        try:
            parts = ", ".join(f"{k}={v}" for k, v in filters.items() if v not in (None, "", []))
            if parts:
                lines.append(f"- **Active filters:** {parts}")
        except Exception:  # noqa: BLE001
            lines.append(f"- **Active filters:** {filters!r}")
    selection = page_context.get("selection")
    if selection:
        try:
            n = len(selection) if hasattr(selection, "__len__") else None
            if isinstance(selection, list) and n:
                lines.append(f"- **Selected items ({n}):** {selection[:5]}{' …' if n > 5 else ''}")
            else:
                lines.append(f"- **Selection:** {selection!r}")
        except Exception:  # noqa: BLE001
            lines.append(f"- **Selection:** {selection!r}")
    extra = {k: v for k, v in page_context.items() if k not in {"path", "route", "label", "summary", "filters", "selection"}}
    for k, v in extra.items():
        if v in (None, "", [], {}):
            continue
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) if len(lines) > 2 else None


def _build_agent_identity_primer(agent: Agent) -> str:
    """Baseline persona injected BEFORE the user's system_prompt.

    Tells the model two things it had no way to know before:
      1. It's running inside Securo — an open-source, self-hosted
         personal-finance app — and what kind of help that implies.
      2. Its own name and stated role, taken from the agent row itself
         (the user picked them in the UI).

    The user's `system_prompt` then runs AFTER this, so it can extend
    or override anything here without losing the product framing.
    """
    name = (agent.name or "Assistant").strip()
    description = (agent.description or "").strip()
    lines = [
        "## Who you are",
        f"You are **{name}**, an AI assistant running inside **Securo** — an "
        "open-source, self-hosted personal-finance app the user owns and "
        "runs on their own infrastructure. The user is the owner of the "
        "data you operate on; everything you read/write belongs to them.",
    ]
    if description:
        lines.append(f"\nYour stated role / specialty: {description}")
    lines.append(
        "\nGround rules:\n"
        "- Answer in the user's language (Portuguese, English, Spanish, etc.).\n"
        "- You have tools that read the user's data and `propose_*` tools "
        "that draft changes for the user to approve — those never apply "
        "by themselves.\n"
        "- Be concise, direct, and willing to make calls. Don't pad with "
        "warnings or boilerplate."
    )
    return "\n".join(lines)


def _classify_error(exc: Exception) -> tuple[str, str]:
    # Always include the underlying exception message — without it the
    # UI just shows "unreachable" / "auth" with no clue about the actual
    # cause (wrong model id, expired key, missing endpoint, etc.).
    detail = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, LLMAuthError):
        return ("auth", f"LLM provider rejected the credentials. {detail}")
    if isinstance(exc, LLMRateLimitError):
        return ("rate_limit", f"LLM provider is rate-limiting. {detail}")
    if isinstance(exc, LLMUnavailableError):
        return ("unavailable", f"LLM provider error: {detail}")
    if isinstance(exc, LLMNotSupportedError):
        return ("not_supported", detail)
    if isinstance(exc, LLMError):
        return (exc.code, detail)
    return ("unknown", detail)


class AgentExecutor:
    def __init__(self, *, mcp: Optional[MCPRegistry] = None):
        self.mcp = mcp or MCPRegistry()
        self.settings = get_agent_settings()

    async def run(
        self,
        *,
        session: AsyncSession,
        agent: Agent,
        user_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID] = None,
        conversation_id: uuid.UUID,
        user_message: str,
        channel: str = "web",
        page_context: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[ExecutorEvent]:
        # 1. Persist the user message first so it survives crashes.
        await conversation_service.append_message(
            session, conversation_id=conversation_id, role="user", content=user_message
        )
        await conversation_service.update_title_if_empty(session, conversation_id, user_message)

        # 2. Build the message list. Order, top to bottom:
        #      1. Runtime guardrail (app-level invariants — always)
        #      2. Agent identity primer (who you are + Securo framing)
        #      3. User-defined system_prompt (extends or overrides #2)
        #      4. Auto-context primer (user data: name, currency, accounts)
        #      5. Page-context primer (where the user is right now)
        #      6. Conversation history
        #      7. The new user message (appended in step 4 below)
        history = await conversation_service.list_messages(session, conversation_id, limit=agent.max_history_messages * 2 + 2)
        messages: list[ChatMessage] = []
        # Runtime guardrail goes FIRST and applies to every conversation,
        # regardless of agent settings or per-agent system prompt. Locks
        # in the propose-vs-action framing and the no-silent-substitution
        # rule. Agents can build on top of these but can't undo them.
        messages.append(ChatMessage(role="system", content=_RUNTIME_GUARDRAIL))
        # Agent identity — name + description + Securo framing. Ensures
        # the model knows what product it lives in and what role it was
        # configured for, even when the user leaves system_prompt blank.
        messages.append(ChatMessage(role="system", content=_build_agent_identity_primer(agent)))
        # User-defined system prompt extends/overrides the identity
        # primer. Goes BEFORE the data primers so it shapes the persona,
        # not just the per-turn answer.
        if agent.system_prompt and agent.system_prompt.strip():
            messages.append(ChatMessage(role="system", content=agent.system_prompt))
        # Optional context primer — user name, currency, accounts, etc.
        # Cheap orientation so the agent doesn't need to call list_accounts
        # on every "what's my balance?" question.
        if getattr(agent, "auto_context", True):
            try:
                from app.models.user import User
                user = await session.get(User, user_id)
                if user is not None:
                    primer = await context_service.build_context_primer(
                        session, user, workspace_id=agent.workspace_id
                    )
                    if primer:
                        messages.append(ChatMessage(role="system", content=primer))
            except Exception:  # noqa: BLE001
                logger.exception("context primer failed; continuing without it")
        # Page-context primer — orientation about where the user is when
        # they sent THIS message. Goes after the agent's prompt so the
        # base persona always wins, but before history so prior turns
        # don't overshadow the current page.
        page_primer = _format_page_context(page_context)
        if page_primer:
            messages.append(ChatMessage(role="system", content=page_primer))
        for m in history:
            tcs = []
            for raw in (m.tool_calls or []):
                tcs.append(ToolCall(id=raw.get("id"), name=raw.get("name"), arguments=raw.get("arguments") or {}))
            tool_call_id = (m.tool_result or {}).get("tool_call_id") if m.role == "tool" else None
            content = m.content
            if m.role == "tool":
                # Encode tool result as content for the LLM.
                tr = m.tool_result or {}
                content = tr.get("text") or _safe_json(tr.get("data"))
            messages.append(ChatMessage(
                role=cast(Role, m.role),
                content=content,
                tool_calls=tcs,
                tool_call_id=tool_call_id,
            ))

        # 3. Discover tools from MCP and filter by per-agent whitelist.
        try:
            handles = await self.mcp.discover(
                user_id=user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent.id,
            )
        except Exception:
            logger.exception("MCP discovery failed; running without tools")
            handles = []
        allowed = await agent_service.allowed_tool_pairs(session, agent.id)
        tool_defs = self.mcp.to_provider_tools(handles, allowed=allowed)

        # Resolve provider+model once per request. Monkey-patched in tests
        # via _provider_for; production prefers _provider_and_model_for.
        try:
            provider, model = await _provider_and_model_for(session, agent)
        except Exception:  # noqa: BLE001
            logger.exception("provider resolution failed; falling back to legacy path")
            provider = _provider_for(agent)
            model = _model_for(agent)
        if not model:
            yield ExecutorEvent(
                type="error",
                error_code="config",
                error_message="Agent has no model configured. Pick a connection or set agent.model.",
            )
            yield ExecutorEvent(type="done", finish_reason="error")
            return

        # 4. Tool-calling loop. Cap iterations to prevent runaway agents.
        MAX_ITERS = 6
        for iteration in range(MAX_ITERS):
            text_buf: list[str] = []
            open_calls: dict[str, dict] = {}
            finish_reason = "stop"
            usage_input = 0
            usage_output = 0
            iter_start = time.time()
            try:
                async for chunk in provider.chat_stream(
                    messages,
                    model=model,
                    tools=tool_defs or None,
                    temperature=agent.temperature,
                ):
                    async for ev in _process_chunk(chunk, text_buf, open_calls):
                        yield ev
                    if chunk.type == "finish":
                        finish_reason = chunk.finish_reason or "stop"
                    elif chunk.type == "usage" and chunk.usage:
                        usage_input = chunk.usage.input_tokens
                        usage_output = chunk.usage.output_tokens
            except LLMError as exc:
                # Log the full chain — the user-facing string is short by
                # design, but we want the traceback (and any wrapped
                # httpx error) in the backend logs for debugging.
                logger.exception("LLM provider call failed (kind=%s)", type(exc).__name__)
                code, msg = _classify_error(exc)
                yield ExecutorEvent(type="error", error_code=code, error_message=msg)
                yield ExecutorEvent(type="done", finish_reason="error")
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("provider stream failed")
                yield ExecutorEvent(type="error", error_code="unknown", error_message=str(exc))
                yield ExecutorEvent(type="done", finish_reason="error")
                return

            assistant_text = "".join(text_buf)
            assembled_calls: list[ToolCall] = []
            for tc in open_calls.values():
                import json
                try:
                    args = json.loads(tc["args_buf"]) if tc["args_buf"] else {}
                except json.JSONDecodeError:
                    args = {"_raw": tc["args_buf"]}
                assembled_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

            # Persist assistant turn.
            assistant_msg = await conversation_service.append_message(
                session,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text or None,
                tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in assembled_calls] or None,
                input_tokens=usage_input or None,
                output_tokens=usage_output or None,
            )
            messages.append(ChatMessage(role="assistant", content=assistant_text or None, tool_calls=assembled_calls))

            # Record one usage row per provider call. Best-effort: a logging
            # failure should never break the user's chat.
            try:
                await usage_service.record_usage(
                    session,
                    user_id=user_id,
                    agent_id=agent.id,
                    conversation_id=conversation_id,
                    message_id=assistant_msg.id,
                    provider=provider.name,
                    model=model,
                    kind="chat",
                    input_tokens=usage_input,
                    output_tokens=usage_output,
                    latency_ms=int((time.time() - iter_start) * 1000),
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to record llm usage")

            if not assembled_calls:
                # If the model returned nothing at all (no text, no tool
                # call, no usage), the endpoint probably isn't actually
                # an LLM — surface that as a friendly error instead of a
                # silent close that leaves the UI hanging. Most common
                # cause: openai_compatible base_url missing the /v1 path.
                if not assistant_text and not usage_input and not usage_output:
                    yield ExecutorEvent(
                        type="error",
                        error_code="empty_response",
                        error_message=(
                            f"The {provider.name} endpoint returned no content. "
                            "Check that the connection's base URL is correct and points to an OpenAI-compatible /v1 root, "
                            "and that the model name matches what's loaded on the server."
                        ),
                    )
                    yield ExecutorEvent(type="done", finish_reason="error")
                    return
                yield ExecutorEvent(type="done", finish_reason=finish_reason)
                return

            # 5. Run tool calls in parallel, persist + emit results, loop.
            for ev in [ExecutorEvent(type="tool_call", tool_name=c.name, tool_args=c.arguments) for c in assembled_calls]:
                yield ev

            results = await asyncio.gather(*[
                _safe_call_tool(self.mcp, c, user_id=user_id, workspace_id=workspace_id, conversation_id=conversation_id, agent_id=agent.id)
                for c in assembled_calls
            ])
            for c, res in zip(assembled_calls, results):
                # Two views of the same result:
                #   - `summary` is a SHORT preview for the UI chip header
                #     (e.g. "5 items returned"). Truncating this is fine.
                #   - `llm_content` is the FULL JSON for the model to read.
                #     Truncating this is what caused the model to think the
                #     data was incomplete and report fake "truncated" rows.
                summary = _summarize_result(res)
                llm_content = _safe_json(res.get("data") if res.get("data") is not None else res.get("text"))
                yield ExecutorEvent(type="tool_result", tool_name=c.name, tool_result=summary)
                await conversation_service.append_message(
                    session,
                    conversation_id=conversation_id,
                    role="tool",
                    content=llm_content,
                    tool_result={
                        "tool_call_id": c.id,
                        "name": c.name,
                        "data": summary.get("data"),
                        "ok": summary.get("ok", False),
                    },
                )
                messages.append(ChatMessage(
                    role="tool",
                    content=llm_content,
                    tool_call_id=c.id,
                    name=c.name,
                ))

        # Reached the tool-call ceiling without the model producing a final
        # answer. Emit a visible fallback so the UI doesn't show an empty
        # assistant bubble, and persist it so the conversation has a
        # readable transcript. The friendliest message reuses any text we
        # accumulated mid-loop if there is some.
        fallback = (
            "Não consegui completar essa consulta — pedi muitas ferramentas em sequência "
            "e o limite foi atingido. Reformule a pergunta de forma mais específica e eu tento "
            "de novo (por exemplo, restrinja a um período ou a uma categoria)."
        )
        yield ExecutorEvent(type="text_delta", text=fallback)
        await conversation_service.append_message(
            session, conversation_id=conversation_id, role="assistant", content=fallback,
        )
        yield ExecutorEvent(type="error", error_code="max_iterations", error_message="Agent reached its tool-call limit.")
        yield ExecutorEvent(type="done", finish_reason="max_iterations")


async def _process_chunk(chunk: ChatChunk, text_buf: list[str], open_calls: dict[str, dict]) -> AsyncIterator[ExecutorEvent]:
    if chunk.type == "text_delta" and chunk.text:
        text_buf.append(chunk.text)
        yield ExecutorEvent(type="text_delta", text=chunk.text)
    elif chunk.type == "tool_call_start" and chunk.tool_call_id:
        open_calls[chunk.tool_call_id] = {
            "id": chunk.tool_call_id,
            "name": chunk.tool_name or "",
            "args_buf": "",
        }
    elif chunk.type == "tool_call_args_delta" and chunk.tool_call_id:
        tc = open_calls.setdefault(chunk.tool_call_id, {"id": chunk.tool_call_id, "name": "", "args_buf": ""})
        tc["args_buf"] += chunk.args_delta or ""


async def _safe_call_tool(
    mcp: MCPRegistry,
    call: ToolCall,
    *,
    user_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    conversation_id: uuid.UUID,
    agent_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    started = time.time()
    try:
        return await mcp.call(
            wire_name=call.name,
            arguments=call.arguments,
            user_id=user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool call %s failed", call.name)
        return {"ok": False, "data": None, "text": f"Tool error: {exc}", "elapsed_ms": int((time.time() - started) * 1000)}


def _summarize_result(res: dict[str, Any]) -> dict[str, Any]:
    """Compact summary used by the UI tool-call chip — just a one-line
    hint plus the structured `data` (which the chip's expand view shows
    in full). Never feed this `text` back to the LLM directly; the LLM
    needs the complete payload, see executor.run().
    """
    data = res.get("data")
    short = ""
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            total = data.get("total")
            n = total if isinstance(total, int) else len(data["items"])
            short = f"{n} item(s) returned"
        elif "error" in data:
            short = f"error: {data['error']}"
        elif "kind" in data:
            short = f"{data['kind']}"
    return {
        "ok": bool(res.get("ok", False)),
        "data": data,
        "text": short or None,
    }


def _safe_json(obj: Any) -> str:
    """Serialize tool data for the LLM to read. No length cap — the
    model needs the full payload or it'll hallucinate truncation."""
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)
