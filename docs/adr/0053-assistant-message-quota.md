# 0053. A monthly limit on assistant messages that use the instance's model

- Status: Accepted
- Date: 2026-11-27
- Origin: #1968 (a fifth limit on a managed instance: assistant messages per month)
- Extends: [0049](0049-hosted-usage-quotas.md), [0014](0014-ai-agents-module.md)

## Context

ADR 0049 holds each account on a managed instance to four counters, all of them things the account keeps: workspaces, bank connections, members. The assistant is different. When an agent answers through the instance's own LLM connection, every message the person sends costs the operator money, and nothing an account keeps grows with it. A ceiling on that needs a count over time, not over rows that exist.

An agent can also answer through an LLM connection the account added itself, with its own key. Those messages cost the operator nothing, and limiting them would only take away something the person pays for.

## Decision

**A fifth counter, `max_assistant_messages_per_month`,** joins the four of ADR 0049 in `COUNTERS` (`backend/app/services/quota_service.py`), with the same rules: a column on `user_quotas`, null means unbounded, the operator writes it through `/api/operator/quotas` and the defaults, `check_fit` reports it and `GET /api/quotas/mine` shows it to the account. It is enforced only with `MANAGED_INSTANCE` and `AGENTS_ENABLED` both on.

**What counts is a message the person sends that the instance's connection answers.** Each row of `agent_messages` keeps one fact, `instance_llm` (migration 138), true when the agent resolved to no LLM connection of the workspace's own and so ran on the instance's (`uses_instance_connection` in `backend/app/agents/runtime/executor.py`). Only rows with role `user` and `instance_llm` true are counted. Tool calls, replies and the model's retries happen inside the turn and are not counted; the turn is the unit the person sees.

**A message counts once the instance's model has answered it.** The executor stores the person's message first, uncounted, so the transcript survives a crash, and sets `instance_llm` only after the first model call of the turn returns. A turn that stops before that (no model configured, the provider failing) spends nothing, so a fix on the operator's side never finds the month already used up. An agent on the account's own connection never falls back to the instance's model: when that connection can't be built, the turn ends with an error instead of being answered, uncounted, by the instance. For the same reason an `openai_compatible` connection can't have its address cleared.

**Messages from before the limit don't count.** Migration 138 adds `instance_llm` with `false` as its default, so every message already stored reads as uncounted. The first month starts with what is sent after the upgrade, which is the only part anyone could have been told about.

**The number is the workspace owner's.** Messages count against the account that created the workspace they were sent in, by `created_by_user_id`, across all of that account's workspaces, archived ones included: an archived workspace's messages were already spent this month. A member who chats in someone else's workspace spends the owner's number, as a member's seat does in ADR 0049, and their own workspaces' numbers are untouched.

**The month is the instance's calendar month,** in the instance timezone (`get_timezone`, ADR 0009), not each workspace's. One account has one number, and a number that reset at a different hour in each of its workspaces would not be one number. The count reads `created_at` between the first instant of the month and the first instant of the next, both in that zone, converted to UTC; `_month` in `backend/app/services/quota_service.py` computes both bounds, and the definer function below receives them instead of computing its own.

**The gate is the chat route,** `POST /api/agents/{agent_id}/chat` (`backend/app/agents/api/chat.py`), before the conversation is created and before the stream opens, so a refusal answers the same `403 quota_exceeded` as the other counters and leaves no empty conversation behind. The screen reads it from the raw response (`readQuotaRefusalBody` in `frontend/src/lib/quota.ts`) and says which limit filled.

**Left out on purpose:**

- The MCP server (`backend/mcp_server/`) serves outside clients that bring their own model. It never calls the instance's LLM, so nothing there is counted.
- Document reading (ADR 0037) always runs on an LLM connection the workspace chose, never on the instance's.

**Row security needs one more definer function.** `agent_messages` and `agent_conversations` are covered tables, so a session bound to one workspace would count only that workspace and the limit could be dodged by spreading the chats. `securo_owned_assistant_messages(p_user uuid, p_from timestamptz, p_to timestamptz) RETURNS bigint` in `backend/app/core/row_security.py` counts across the owner's workspaces the way `securo_owned_bank_connections` does in ADR 0049: `STABLE SECURITY DEFINER`, a fixed `search_path`, revoked from `PUBLIC`, granted to the restricted role only, and answering one number.

## Consequences

- There is no lock between the count and the message. Requests sent at the same instant can each pass the last free place, so an account can go past its number by as many messages as it sends at once. For a monthly ceiling on cost that is accepted; the other counters keep their lock because one extra workspace or connection stays, while one extra message does not.
- Deleting a conversation deletes its messages, and with them their place in the count. A person can lower their count by deleting chats. That is accepted for now: storing a counter apart from the rows would be the fix, and it is a larger change than the limit itself.
- An agent moved from the instance's connection to the account's own stops counting from the next message; the messages already sent keep the fact they were sent with.
- The fact is stored per message, never as a running total, so changing the limit, the defaults or the timezone rewrites nothing.

## Enforced by

`backend/tests/test_assistant_message_quota.py`: the count across the owner's workspaces, a workspace someone was invited into, only the person's messages, an own connection being free, archived workspaces, the month boundary in a timezone other than UTC, `check_fit`, the chat gate off without either switch, refused past the limit with no conversation written, an agent on the account's own connection never refused, a member spending the owner's number, a turn with no model or a failed first call not counted, a broken own connection not falling back, and an `openai_compatible` address that can't be cleared. `backend/tests/test_row_security_postgres.py` checks the definer function under the restricted role, its month bounds included.
