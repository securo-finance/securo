# 0014. AI agents are an optional module

- Status: Accepted
- Date: 2026-05-12
- Origin: #186 (agents), #224 (external MCP), #227 (agents per workspace)

## Context

Talking to your finances through an LLM is useful to some people and unwanted by others, for cost and privacy reasons. It also pulls in heavy dependencies (embeddings, provider SDKs, an MCP server).

## Decision

- Everything lives in `backend/app/agents/` (API, models, LLM providers, executor, MCP client, knowledge ingestion) and the separate `backend/mcp_server/`.
- `AGENTS_ENABLED` gates it (ADR 0004): `backend/app/main.py` imports and mounts the agents routers only when it is on. The MCP server runs only under the Compose profile `agents`, bound to `127.0.0.1:8765` by default, always behind a JWT, with no browser origins allowed unless `AGENTS_MCP_ALLOWED_ORIGINS` lists them.
- The MCP signing key has no default value. `AGENTS_MCP_JWT_SECRET` is used when set; otherwise the key is derived from `SECRET_KEY` (`backend/app/agents/mcp/keys.py`). A known public value, or a public or short `SECRET_KEY` to derive from, makes the API and the MCP server refuse to start (amended by ADR 0020: a public value is now ignored and a public `SECRET_KEY` replaced, so only a short `SECRET_KEY` of your own still refuses): the key is the only thing between a caller and a user's data, and the user directory gives every registered user the UUIDs a forged token needs (#1174).
- The Helm chart does not route `/mcp` through the Ingress or the HTTPRoute unless `mcpServer.publicRoute` is on. Publishing an authenticated endpoint is a deliberate deployment choice.
- LLM providers (Anthropic, OpenAI, OpenAI-compatible, Ollama) are configured by the user per connection; nothing is called until one is. A local model through Ollama keeps everything on the machine.
- Agents reach financial data through the same services and the same workspace and scope checks as the API (ADR 0002, 0011, 0012).

## Consequences

- The agents tables and the `vector` extension exist on every instance, because their migrations always run (noted in ADR 0004).
- New agent tools call services; they never query tables directly or bypass access token scopes.

## Enforced by

`tests/test_agents_*.py`, `tests/test_mcp_*.py`, `tests/test_mcp_token_scoping.py`, `tests/test_mcp_jwt_secret.py`, `tests/test_mcp_default_secret_exposure.py`. The test suite forces `AGENTS_ENABLED=true`, so the off path is not covered by tests.
